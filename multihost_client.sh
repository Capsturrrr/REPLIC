#!/bin/bash
# Run on the CLIENT machine (the sending end), with multihost_server.sh running on the other machine.
#   bash multihost_client.sh SERVER_IP [REPS] [LABEL]      e.g.  bash multihost_client.sh 100.101.102.103 5 tailscale_laptop_phone
# Sends the same 2 MB (4000 x 512 B) with kernel TCP and with RUDP (tuned AIMD, CUBIC, BBR-style, neural), alternating, REPS times.
# Output: results/multihost_LABEL.txt  (send it back). No root needed.
HOST=${1:?server ip}; REPS=${2:-5}; LABEL=${3:-run}; PK=4000
cd "$(dirname "$0")"; make smart >/dev/null 2>&1
for f in bin/smart_client results/dqn_chat.txt web/tcp_send.py; do [ -e $f ] || { echo "missing $f"; exit 1; }; done
OUT=results/multihost_$LABEL.txt; : > $OUT; log() { echo "$@" | tee -a $OUT; }
head -c $((PK*512)) /dev/urandom > /tmp/mh_payload.bin
log "multi-machine run: client $(hostname) -> server $HOST, $PK x 512 B, $REPS reps, $(date -u +%FT%TZ)"
if ! python3 -c "import socket,sys; socket.create_connection((sys.argv[1],9871),5).close()" $HOST 2>/dev/null; then
  echo "CANNOT REACH $HOST:9871 (TCP). Check: (1) 'bash multihost_server.sh' is running on the server, (2) $HOST is the server's real address"
  echo "(WSL 172.x addresses do NOT work from another machine; use Tailscale 100.x or a LAN IP), (3) firewall / captive-portal Wi-Fi / client isolation."
  exit 2
fi
log "ping: $(ping -c 10 -q $HOST 2>&1 | tail -2 | tr '\n' ' ')"
for i in $(seq $REPS); do
  t=$(python3 web/tcp_send.py $HOST 9871 $((PK*512)) 2>&1); log "rep $i TCP seconds: $t"
  for m in aimd cubic bbr deep; do
    case $m in
      aimd)  a="--mode aimd --maxcwnd 128 --ssthresh 128" ;;
      cubic) a="--mode cubic --maxcwnd 128 --ssthresh 128" ;;
      bbr)   a="--mode bbr" ;;
      deep)  a="--mode deep --dfile results/dqn_chat.txt" ;;
    esac
    r=$(timeout 300 ./bin/smart_client $a --emu 0 --host $HOST --scenario mh --file /tmp/mh_payload.bin 2>&1 | grep RESULT)
    log "rep $i RUDP $m: $r"
  done
done
log "== done: send results/multihost_$LABEL.txt back =="
