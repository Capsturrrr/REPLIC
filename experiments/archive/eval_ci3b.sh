#!/bin/bash
# Go-Back-N reference arms for experiments/archive/eval_ci3.sh, against the PLAIN server (experiments/archive/eval_ci3.sh's sack0 arms used the SACK-capable server).
# usage: bash experiments/archive/eval_ci3b.sh [runs] -> results/eval_ci3b_<scenario>.csv
export REPLIC_CLASSIC=1   # keep the original agent settings (cap 32, no hand-over) so these results stay reproducible
export REPLIC_CLASSIC=1
cd "$(dirname "$0")/../.."; RUNS=${1:-8}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"
P[reorder]="--delay 25 --jitter 20 --loss 2 --rate 800 --queue 50 --reorder 1"
port=9800
for sc in 5g lossy reorder sat; do
  port=$((port+1)); gcc -DREPLIC_GBN -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_ci3b_$sc.csv
    for i in $(seq 1 $RUNS); do for m in aimd deep; do
      ./bin/smart_client --gbn --mode $m --sack 0 --init 10 --port $port ${P[$sc]} --scenario $sc --packets 1200 --qfile results/q_chat.txt --dfile results/dqn_chat.txt --seed $((4000+i)) | grep RESULT | sed "s/^RESULT,$sc,$m,/RESULT,$sc,$m-plain,/" >> results/eval_ci3b_$sc.csv
    done; done; kill $SP )
done
echo DONE
