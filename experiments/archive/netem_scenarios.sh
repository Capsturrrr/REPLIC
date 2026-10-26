#!/bin/bash
# Run on YOUR WSL (needs sudo + iproute2). NOT run in the cloud build.
# Applies a real tc/netem impairment on loopback, then runs smart_client with --emu 0
# so the kernel (not the in-program emulator) provides delay/loss/rate.
#   usage: sudo bash experiments/archive/netem_scenarios.sh 5g|lossy|sat
sc=${1:-5g}
case $sc in
  5g)    D=10;  J=3;  L=0.2; R=12mbit ;;
  lossy) D=25;  J=10; L=2;   R=6mbit  ;;
  sat)   D=150; J=10; L=0.3; R=3mbit  ;;
esac
tc qdisc del dev lo root 2>/dev/null
tc qdisc add dev lo root netem delay ${D}ms ${J}ms loss ${L}% rate $R limit 60
./bin/server >/dev/null 2>&1 & SP=$!
sleep 0.5
for m in aimd rl; do
  ./bin/smart_client --mode $m --emu 0 --scenario $sc --packets 1200 --qfile results/q_$sc.txt | grep RESULT
done
kill $SP; tc qdisc del dev lo root
