#!/bin/bash
# One flow over a bottleneck that also carries bursty cross traffic (exponential on/off bursts), real sockets via bin/impair.
# Usage: ./eval_cross.sh [runs]     Output: results/eval_cross.csv and results/eval_cross_summary.txt
cd "$(dirname "$0")"; RUNS=${1:-8}; make smart impair >/dev/null 2>&1
PATHARGS="--delay 30 --jitter 3 --loss 0.1 --rate 1000 --queue 60 --cross 300:500:700"
gcc -DSERVER_PORT=9951 -o bin/server_x src/server.c src/common.c
arm() { case $1 in aimd_32) echo "--mode aimd --init 10 --maxcwnd 32";; aimd_128) echo "--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";; cubic_128) echo "--mode cubic --init 10 --maxcwnd 128 --ssthresh 128";; deep) echo "--mode deep --init 10 --dfile results/dqn_chat.txt";; polite) echo "--mode deep --init 10 --dfile results/dqn_chat.txt --polite 1.3";; esac; }
rm -f results/eval_cross.csv
for i in $(seq 1 $RUNS); do for a in aimd_32 aimd_128 cubic_128 deep polite; do
  RUDP_QUIET=1 ./bin/server_x >/dev/null 2>&1 & S=$!
  ./bin/impair 9952:9951 $PATHARGS --seed $i >/dev/null 2>&1 & PX=$!; sleep 0.3
  R=$(timeout 200 ./bin/smart_client $(arm $a) --emu 0 --port 9952 --packets 2500 --seed $i 2>&1 | grep RESULT)
  kill $PX $S 2>/dev/null; wait $PX $S 2>/dev/null
  echo "$a,$i,$(echo $R | cut -d, -f4),$(echo $R | cut -d, -f7)" >> results/eval_cross.csv
done; done
python3 - <<'P' | tee results/eval_cross_summary.txt
import csv, statistics as st, math
rows=[l.strip().split(',') for l in open('results/eval_cross.csv')]
arms=["aimd_32","aimd_128","cubic_128","deep","polite"]
by={a:{int(r[1]):float(r[2])/1000 for r in rows if r[0]==a and r[2]} for a in arms}
T={0.025:12.706,0.05:4.303}
print("Completion time (s), 2500 packets, bursty cross traffic on the bottleneck, real sockets; mean +- 95% CI, ratio to tuned AIMD (paired)")
for a in arms:
    v=list(by[a].values()); n=len(v)
    t=st.mean(v); h=(2.0 if n>8 else 2.4)*st.stdev(v)/math.sqrt(n) if n>1 else 0
    common=[i for i in by[a] if i in by["aimd_128"]]
    lr=[math.log(by[a][i]/by["aimd_128"][i]) for i in common]
    print(f"{a:10s} n={n} {t:6.2f} +- {h:4.2f}   ratio vs tuned AIMD {math.exp(st.mean(lr)):.3f}")
P
