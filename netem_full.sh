#!/bin/bash
# Kernel-netem evaluation: TCP, plain UDP and RUDP (AIMD, CUBIC, neural) on the same path.
#   sudo bash netem_full.sh lossy|5g|sat|reorder [REPS]   (TCP_ONLY=1 sudo -E ... skips RUDP)
# TCP/UDP use 512-byte payloads, the same as RUDP, so every protocol moves the same bytes.      (build first: make smart)
# Writes results/netem_<scenario>.txt (also printed). Removes the netem rule on exit.
sc=${1:-lossy}; REPS=${2:-5}; PKTS=500
case $sc in
  5g)      D=10;  J=3;  L=0.2; R=12mbit; X="" ;;
  lossy)   D=25;  J=10; L=2;   R=6mbit;  X="" ;;
  sat)     D=150; J=10; L=0.3; R=3mbit;  X="" ;;
  reorder) D=50;  J=0;  L=1;   R=6mbit;  X="reorder 25% 50%" ;;
  *) echo "scenario must be lossy|5g|sat|reorder"; exit 1 ;;
esac
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")"
for f in bin/server bin/smart_client results/dqn_chat.txt web/real_baselines.py; do
  [ -e $f ] || { echo "missing $f (run: make smart)"; exit 1; }
done
OUT=results/netem_$sc.txt; : > $OUT
SP=""
cleanup() { [ -n "$SP" ] && kill $SP 2>/dev/null; tc qdisc del dev lo root 2>/dev/null; }
trap cleanup EXIT
log() { echo "$@" | tee -a $OUT; }
tc qdisc del dev lo root 2>/dev/null
tc qdisc add dev lo root netem delay ${D}ms ${J}ms $X loss ${L}% rate $R limit 60 || { echo "tc failed (WSL2 needs the netem kernel module)"; exit 1; }
log "scenario=$sc delay=${D}ms jitter=${J}ms loss=${L}% rate=$R packets=$PKTS reps=$REPS"
log "== kernel TCP and plain UDP =="
for i in $(seq $REPS); do python3 web/real_baselines.py $PKTS 512 2>&1 | tee -a $OUT; done
[ -n "$TCP_ONLY" ] && { log "== done (TCP_ONLY) =="; exit 0; }
./bin/server >/dev/null 2>&1 \& SP=$!
sleep 0.5
log "== RUDP (RESULT: scenario,mode,ms,packets,sent,retx,timeouts,fast,avg_rtt,min_rtt,pps,cwnd,reward) =="
for i in $(seq $REPS); do
  for m in aimd cubic deep; do
    extra=""; [ $m = deep ] && extra="--dfile results/dqn_chat.txt"
    ./bin/smart_client --mode $m $extra --emu 0 --scenario lossy --packets $PKTS 2>&1 | grep RESULT | tee -a $OUT
  done
done
log "== done: send results/netem_$sc.txt back =="
