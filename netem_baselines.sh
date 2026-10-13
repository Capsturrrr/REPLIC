#!/bin/bash
# Run on YOUR WSL/Linux (needs sudo + iproute2). Real kernel TCP vs plain UDP vs Poise on the same netem path.
#   sudo bash netem_baselines.sh lossy        (or 5g, sat, reorder)   -- build first: make smart
sc=${1:-lossy}
case $sc in
  5g)    D=10;  J=3;  L=0.2; R=12mbit ;;
  lossy) D=25;  J=10; L=2;   R=6mbit  ;;
  sat)   D=150; J=10; L=0.3; R=3mbit  ;;
  reorder) D=50; J=0; L=1;  R=6mbit  ;;   # 25% of packets jump the queue (reordering)
esac
tc qdisc del dev lo root 2>/dev/null
if [ $sc = reorder ]; then tc qdisc add dev lo root netem delay ${D}ms reorder 25% 50% loss ${L}% rate $R limit 60
else tc qdisc add dev lo root netem delay ${D}ms ${J}ms loss ${L}% rate $R limit 60; fi
echo "== kernel TCP and plain UDP =="; python3 web/real_baselines.py 500
echo "== Poise =="; ./bin/server >/dev/null 2>&1 & SP=$!
sleep 0.5
for m in aimd rl; do ./bin/smart_client --mode $m --emu 0 --scenario ${sc/reorder/lossy} --packets 500 --qfile results/q_${sc/reorder/lossy}.txt | grep RESULT; done
kill $SP; tc qdisc del dev lo root
