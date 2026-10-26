#!/bin/bash
# Two flows with UNEQUAL round-trip times (flow B has +XD ms extra on the ACK path) share one bottleneck. Real sockets, bin/impair.
cd "$(dirname "$0")/.."; RUNS=${1:-6}; make smart impair >/dev/null 2>&1
BOT="--delay 30 --jitter 3 --loss 0.1 --rate 1000 --queue 60"
gcc -DSERVER_PORT=9931 -o bin/server_fa src/server.c src/common.c; gcc -DSERVER_PORT=9932 -o bin/server_fb src/server.c src/common.c
arm() { case $1 in aimd_128) echo "--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";; deep) echo "--mode deep --init 10 --dfile results/dqn_chat.txt";; esac; }
rm -f results/eval_fair_rtt.csv
for XD in 0 60 150; do for pair in aimd_128:aimd_128 deep:deep; do
  A=${pair%%:*}; B=${pair##*:}
  for i in $(seq 1 $RUNS); do
    REPLIC_QUIET=1 ./bin/server_fa >/dev/null 2>&1 & S1=$!; REPLIC_QUIET=1 ./bin/server_fb >/dev/null 2>&1 & S2=$!
    ./bin/impair 9933:9931 9934:9932 $BOT --xdelay $XD --seed $i > /tmp/fr_share.txt 2>/dev/null & PX=$!; sleep 0.3
    timeout 200 ./bin/smart_client $(arm $A) --emu 0 --port 9933 --packets 2500 --seed $i > /tmp/fr_a.txt 2>&1 & C1=$!
    timeout 200 ./bin/smart_client $(arm $B) --emu 0 --port 9934 --packets 2500 --seed $((i+50)) > /tmp/fr_b.txt 2>&1 & C2=$!
    wait $C1 $C2; kill $PX $S1 $S2 2>/dev/null; wait $PX $S1 $S2 2>/dev/null
    read _ a b < /tmp/fr_share.txt
    echo "$A,$B,xd$XD,$i,$a,$b" >> results/eval_fair_rtt.csv
  done
done; done
python3 - <<'P' | tee results/eval_fair_rtt_summary.txt
import csv, collections, statistics as st
d=collections.defaultdict(list)
for r in csv.reader(open("results/eval_fair_rtt.csv")):
    if len(r)<6 or not r[4].isdigit() or not r[5].isdigit(): continue
    a,b=int(r[4]),int(r[5]); d[(r[0],r[2])].append((a,b))
print("pair        extra RTT(B)  share A  share B  Jain  n   (flow B has xd ms extra RTT on top of the ~66 ms base; flow A is the short-RTT flow)")
for (p,x),v in d.items():
    sa=st.mean(a/(a+b) for a,b in v); j=st.mean((a+b)**2/(2*(a*a+b*b)) for a,b in v)
    print(f"{p:11s} {x:>8s}   {sa*100:5.0f}%  {100-sa*100:5.0f}%  {j:.3f}  {len(v)}")
P
