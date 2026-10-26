#!/bin/bash
# Repeated evaluation with many seeds, for confidence intervals. usage: bash experiments/archive/eval_ci.sh [runs]   (frozen Q-tables, 1200 packets)
export REPLIC_CLASSIC=1   # keep the original agent settings (cap 32, no hand-over) so these results stay reproducible
export REPLIC_CLASSIC=1
cd "$(dirname "$0")/../.."; RUNS=${1:-20}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"
port=9200
for sc in 5g lossy sat; do
  port=$((port+1)); gcc -DREPLIC_GBN -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_ci_$sc.csv
    for i in $(seq 1 $RUNS); do for m in aimd rl; do
      ./bin/smart_client --gbn --mode $m --port $port ${P[$sc]} --scenario $sc --packets 1200 --qfile results/q_$sc.txt --seed $((2000+i)) | grep RESULT >> results/eval_ci_$sc.csv
    done; done; kill $SP ) &
done
wait; echo DONE
