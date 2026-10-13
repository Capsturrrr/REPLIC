#!/bin/bash
# Kernel-netem evaluation: TCP, plain UDP and RUDP (AIMD, CUBIC, neural) on the same path.
#   sudo bash netem_full.sh lossy|5g|sat|reorder [REPS]   (TCP_ONLY=1 sudo -E ... skips RUDP)
# TCP/UDP use 512-byte payloads, the same as RUDP, so every protocol moves the same bytes.      (build first: make smart)
# Writes results/netem_<scenario>.txt (also printed). Removes the netem rule on exit.
sc=${1:-lossy}; REPS=${2:-5}; PKTS=500
case $sc in
  5g)      D=10;  J=3;  L=0.2; R=12mbit; Q=60;  X="" ;;
  lossy)   D=25;  J=10; L=2;   R=6mbit;  Q=50;  X="" ;;
  sat)     D=150; J=10; L=0.3; R=3mbit;  Q=100; X="" ;;
  reorder) D=50;  J=0;  L=1;   R=6mbit;  Q=50;  X="reorder 25% 50%" ;;
  *) echo "scenario must be lossy|5g|sat|reorder"; exit 1 ;;
esac
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")"
for f in bin/server bin/smart_client results/dqn_chat.txt web/real_baselines.py; do
  [ -e $f ] || { echo "missing $f (run: make smart)"; exit 1; }
done
# RUDP must send REAL 512-byte payloads like TCP and UDP (without --file the sender uses synthetic 40-byte packets)
PL=/tmp/rudp_payload_${PKTS}.bin; head -c 256000 /dev/urandom > $PL
OUT=results/netem_$sc.txt; : > $OUT
SP=""
OLDMTU=$(cat /sys/class/net/lo/mtu 2>/dev/null || echo 65536)
cleanup() { [ -n "$SP" ] && kill $SP 2>/dev/null; tc qdisc del dev lo root 2>/dev/null; ip link set dev lo mtu $OLDMTU 2>/dev/null; }
trap cleanup EXIT
log() { echo "$@" | tee -a $OUT; }
tc qdisc del dev lo root 2>/dev/null
# loopback normally has a 64 KB MTU; the token bucket cannot pass packets larger than its burst, so use an Ethernet-sized MTU for the test
ip link set dev lo mtu 1500 || { echo "could not set lo mtu"; exit 1; }
# Delay, jitter, loss and reordering in netem (large limit so packets in flight are never dropped); the bottleneck is a separate
# token-bucket stage with a drop-tail queue of Q packets (about 600 bytes each), like the emulated path in the simulator.
tc qdisc add dev lo root handle 1: netem delay ${D}ms ${J}ms $X loss ${L}% limit 100000 || { echo "tc netem failed (WSL2 needs the netem kernel module)"; exit 1; }
tc qdisc add dev lo parent 1:1 handle 10: tbf rate $R burst 16kb limit $((Q*600+16000)) || { echo "tc tbf failed"; exit 1; }
log "scenario=$sc delay=${D}ms jitter=${J}ms loss=${L}% rate=$R queue=${Q}pkts (netem + tbf bottleneck) packets=$PKTS reps=$REPS"
[ -n "$RUDP_ONLY" ] || { log "== kernel TCP and plain UDP =="
for i in $(seq $REPS); do python3 web/real_baselines.py $PKTS 512 2>&1 | tee -a $OUT; done; }
[ -n "$TCP_ONLY" ] && { log "== done (TCP_ONLY) =="; exit 0; }
./bin/server >/dev/null 2>&1 & SP=$!
sleep 0.5
log "== RUDP (RESULT: scenario,mode,ms,packets,sent,retx,timeouts,fast,avg_rtt,min_rtt,pps,cwnd,reward) =="
for i in $(seq $REPS); do
  # aimd0 = untuned AIMD (init 1, ssthresh 16); aimd = tuned AIMD used in the report (cap 128, ssthresh 128); aimd10 = tuned + initial window 10 (TCP-like)
  for m in aimd0 aimd aimd10 deep; do
    case $m in
      aimd0)  a="--mode aimd" ;;
      aimd)   a="--mode aimd --maxcwnd 128 --ssthresh 128" ;;
      aimd10) a="--mode aimd --maxcwnd 128 --ssthresh 128 --init 10" ;;
      deep)   a="--mode deep --dfile results/dqn_chat.txt" ;;
    esac
    echo -n "$m " | tee -a $OUT
    ./bin/smart_client $a --emu 0 --scenario lossy --file $PL 2>&1 | grep RESULT | tee -a $OUT
  done
done
log "== done: send results/netem_$sc.txt back =="
