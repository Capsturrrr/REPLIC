#!/bin/bash
# Coexistence with kernel TCP: a bulk TCP flow and one RUDP transfer share the SAME netem + tbf bottleneck at the same time.
#   sudo bash netem_coexist.sh 5g|lossy|sat [REPS]          (build first: make smart)
# For every RUDP arm the script reports goodput of each flow over the window in which both were running
# (TCP bytes counted by its receiver; RUDP = packets*512 B / completion time), and the RUDP share of the sum.
# Controls: TCP+TCP (a second kernel TCP flow instead of RUDP) and TCP alone / RUDP alone. NO_TC=1 skips tc (script self-test, no bottleneck).
sc=${1:-5g}; REPS=${2:-3}; PKTS=${PKTS:-4000}
case $sc in
  5g)   D=10;  J=3;  L=0.2; R=12mbit; Q=60  ;;
  lossy) D=25; J=10; L=2;   R=6mbit;  Q=50  ;;
  sat)  D=150; J=10; L=0.3; R=3mbit;  Q=100 ;;
  *) echo "scenario must be 5g|lossy|sat"; exit 1 ;;
esac
[ -n "$NO_TC" ] || [ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")"
for f in bin/server bin/smart_client results/dqn_chat.txt web/tcp_bulk.py; do [ -e $f ] || { echo "missing $f (run: make smart)"; exit 1; }; done
# RUDP must send REAL 512-byte payloads like TCP and UDP (without --file the sender uses synthetic 40-byte packets)
PL=/tmp/rudp_payload_${PKTS}.bin; head -c $((PKTS*512)) /dev/urandom > $PL
pkill -f web/tcp_bulk.py 2>/dev/null; pkill -x server 2>/dev/null; pkill -x smart_client 2>/dev/null; sleep 0.5   # clear leftovers from an interrupted earlier run
OUT=results/netem_coexist_$sc.txt; : > $OUT
OLDMTU=$(cat /sys/class/net/lo/mtu 2>/dev/null || echo 65536); SP=""; TP=""; TP2=""
cleanup() { for p in $SP $TP $TP2; do kill $p 2>/dev/null; done; [ -n "$NO_TC" ] || { tc qdisc del dev lo root 2>/dev/null; ip link set dev lo mtu $OLDMTU 2>/dev/null; }; }
trap cleanup EXIT
log() { echo "$@" | tee -a $OUT; }
if [ -z "$NO_TC" ]; then
  tc qdisc del dev lo root 2>/dev/null
  ip link set dev lo mtu 1500 || { echo "could not set lo mtu"; exit 1; }
  tc qdisc add dev lo root handle 1: netem delay ${D}ms ${J}ms loss ${L}% limit 100000 || { echo "tc netem failed"; exit 1; }
  tc qdisc add dev lo parent 1:1 handle 10: tbf rate $R burst 16kb limit $((Q*600+16000)) || { echo "tc tbf failed"; exit 1; }
fi
log "scenario=$sc delay=${D}ms jitter=${J}ms loss=${L}% rate=$R queue=${Q}pkts; RUDP transfer=$PKTS packets of 512 B; reps=$REPS; kernel TCP congestion control: $(cat /proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null)"
tcp_bytes() { # LOGFILE T0 T1 -> bytes received between T0 and T1
  python3 - "$1" "$2" "$3" <<'P'
import sys
t0, t1 = float(sys.argv[2]), float(sys.argv[3]); pts = [tuple(map(float, l.split())) for l in open(sys.argv[1]) if len(l.split()) == 2]
def at(t):
    b = 0
    for tt, g in pts:
        if tt <= t: b = g
    return b
print(int(at(t1) - at(t0)))
P
}
./bin/server >/dev/null 2>&1 & SP=$!; sleep 0.5
run_pair() { # ARM-ARGS or "tcp" -> prints "tcp_mbps other_mbps"
  rm -f /tmp/coex_tcp.log /tmp/coex_tcp2.log
  python3 web/tcp_bulk.py 9861 /tmp/coex_tcp.log 512 & TP=$!; sleep 1.5      # let TCP ramp up
  T0=$(date +%s.%N)
  if [ "$1" = tcp ]; then
    python3 web/tcp_bulk.py 9862 /tmp/coex_tcp2.log 512 & TP2=$!; sleep ${TCP_SECS:-8}; T1=$(date +%s.%N)
    b2=$(tcp_bytes /tmp/coex_tcp2.log $T0 $T1); kill $TP2 2>/dev/null; TP2=""; ms=$(python3 -c "print(($T1-$T0)*1000)")
    pk=$((b2/512))
  else
    R=$(./bin/smart_client $1 --emu 0 --scenario coex --file $PL 2>&1 | grep RESULT); T1=$(date +%s.%N)
    ms=$(echo "$R" | cut -d, -f4); pk=$PKTS
  fi
  b1=$(tcp_bytes /tmp/coex_tcp.log $T0 $T1); kill $TP 2>/dev/null; wait $TP 2>/dev/null; TP=""
  python3 -c "w=($T1-$T0); print(f'{$b1*8/w/1e6:.2f} {$pk*512*8/($ms/1000)/1e6:.2f}')"
}
log "== link capacity: TCP alone, then RUDP alone (Mbit/s goodput) =="
rm -f /tmp/coex_tcp.log; python3 web/tcp_bulk.py 9861 /tmp/coex_tcp.log 512 & TP=$!; sleep 1.5; T0=$(date +%s.%N); sleep 6; T1=$(date +%s.%N)
log "TCP alone: $(python3 -c "print(f'{$(tcp_bytes /tmp/coex_tcp.log $T0 $T1)*8/($T1-$T0)/1e6:.2f}')") Mbit/s"; kill $TP; wait $TP 2>/dev/null; TP=""
for m in aimd deep; do
  case $m in aimd) a="--mode aimd --maxcwnd 128 --ssthresh 128" ;; deep) a="--mode deep --dfile results/dqn_chat.txt" ;; esac
  R=$(./bin/smart_client $a --emu 0 --scenario coex --file $PL 2>&1 | grep RESULT); ms=$(echo "$R" | cut -d, -f4)
  log "RUDP $m alone: $(python3 -c "print(f'{$PKTS*512*8/($ms/1000)/1e6:.2f}')") Mbit/s"
done
log "== together (columns: TCP Mbit/s, other flow Mbit/s, other flow's share of the sum) =="
for arm in tcp aimd deep; do
  case $arm in tcp) a=tcp ;; aimd) a="--mode aimd --maxcwnd 128 --ssthresh 128" ;; deep) a="--mode deep --dfile results/dqn_chat.txt" ;; esac
  for i in $(seq $REPS); do
    read t o < <(run_pair "$a")
    log "TCP vs $arm  rep $i:  TCP $t   $arm $o   $arm share $(python3 -c "print(f'{100*$o/($t+$o):.0f}%')")"
  done
done
log "== done: send results/netem_coexist_$sc.txt back =="
