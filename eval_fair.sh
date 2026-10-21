#!/bin/bash
# Two flows share one bottleneck (real sockets, bin/impair). Packets that each flow got through the bottleneck when the
# first flow finished give the bandwidth share; Jain's index = (a+b)^2 / (2(a^2+b^2)), 1.0 = perfectly even.
cd "$(dirname "$0")"; RUNS=${1:-5}; make smart impair >/dev/null 2>&1
BOT="--delay 30 --jitter 3 --loss 0.1 --rate 1000 --queue 60"
gcc -DSERVER_PORT=9921 -o bin/server_fa src/server.c src/common.c; gcc -DSERVER_PORT=9922 -o bin/server_fb src/server.c src/common.c
arm() { case $1 in aimd_32) echo "--mode aimd --init 10 --maxcwnd 32";; aimd_128) echo "--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";; cubic_128) echo "--mode cubic --init 10 --maxcwnd 128 --ssthresh 128";; deep) echo "--mode deep --init 10 --dfile results/dqn_chat.txt";; esac; }
rm -f results/eval_fair.csv
for pair in aimd_128:aimd_128 deep:deep deep:aimd_128 deep:aimd_32 cubic_128:aimd_128; do
  A=${pair%%:*}; B=${pair##*:}
  for i in $(seq 1 $RUNS); do
    REPLIC_QUIET=1 ./bin/server_fa >/dev/null 2>&1 & S1=$!; REPLIC_QUIET=1 ./bin/server_fb >/dev/null 2>&1 & S2=$!
    ./bin/impair 9923:9921 9924:9922 $BOT --seed $i > /tmp/fair_share.txt 2>/dev/null & PX=$!; sleep 0.3
    timeout 200 ./bin/smart_client $(arm $A) --emu 0 --port 9923 --packets 2500 --seed $i > /tmp/fair_a.txt 2>&1 & C1=$!
    timeout 200 ./bin/smart_client $(arm $B) --emu 0 --port 9924 --packets 2500 --seed $((i+50)) > /tmp/fair_b.txt 2>&1 & C2=$!
    wait $C1 $C2; kill $PX $S1 $S2 2>/dev/null; wait $PX $S1 $S2 2>/dev/null
    read _ a b < /tmp/fair_share.txt
    ta=$(grep RESULT /tmp/fair_a.txt | cut -d, -f4); tb=$(grep RESULT /tmp/fair_b.txt | cut -d, -f4)
    echo "$A,$B,$i,$a,$b,$ta,$tb" >> results/eval_fair.csv
  done
done
python3 ci_summary_fair.py | tee results/eval_fair_summary.txt
