#!/bin/bash
# Selective ACK in the C transport: AIMD and the neural agent, with and without SACK, plus a reordering path.
# Scenarios run one after another so CPU load does not distort the timings.
# usage: bash eval_ci3.sh [runs]  -> results/eval_ci3_<scenario>.csv ; summarise with python3 ci_summary3.py
export POISE_CLASSIC=1   # keep the original agent settings (cap 32, no hand-over) so these results stay reproducible
export POISE_CLASSIC=1
cd "$(dirname "$0")"; RUNS=${1:-10}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"
P[reorder]="--delay 25 --jitter 20 --loss 2 --rate 800 --queue 50 --reorder 1"
port=9700
for sc in 5g lossy reorder sat; do
  port=$((port+1))
  gcc -DSERVER_PORT=$port -DPOISE_SACK -o bin/server_$port src/server.c src/common.c
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_ci3_$sc.csv
    for i in $(seq 1 $RUNS); do for m in aimd deep; do for sk in 0 1; do
      ./bin/smart_client --mode $m --sack $sk --init 10 --port $port ${P[$sc]} --scenario $sc --packets 1200 --qfile results/q_chat.txt --dfile results/dqn_chat.txt --seed $((4000+i)) | grep RESULT | sed "s/^RESULT,$sc,$m,/RESULT,$sc,$m-sack$sk,/" >> results/eval_ci3_$sc.csv
    done; done; done; kill $SP )
done
echo DONE
