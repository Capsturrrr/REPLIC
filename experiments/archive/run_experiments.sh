#!/bin/bash
# Train Q-learning per scenario, then evaluate AIMD vs frozen RL. Scenarios run in parallel on separate ports.
# usage: bash experiments/archive/run_experiments.sh [train_episodes] [eval_runs]
cd "$(dirname "$0")/../.."; mkdir -p results bin
TRAIN=${1:-30}; EVAL=${2:-10}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"
port=9100
for sc in 5g lossy sat; do
  port=$((port+1))
  gcc -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  (
    TP=500; [ $sc = sat ] && TP=800
    ./bin/server_$port >/dev/null 2>&1 & SP=$!
    sleep 0.3
    rm -f results/q_$sc.txt results/train_$sc.csv results/eval_$sc.csv
    for i in $(seq 1 $TRAIN); do
      e=$(python3 -c "print(max(0.03,0.35-0.008*$i))")
      ./bin/smart_client --mode rl --port $port ${P[$sc]} --scenario $sc --packets $TP --train 1 --eps $e \
         --qfile results/q_$sc.txt --seed $i | grep RESULT | sed "s/^/$i,/" >> results/train_$sc.csv
    done
    for i in $(seq 1 $EVAL); do
      for m in aimd rl; do
        ./bin/smart_client --mode $m --port $port ${P[$sc]} --scenario $sc --packets 1200 --qfile results/q_$sc.txt \
          --seed $((1000+i)) --trace results/trace_${sc}_${m}_$i.csv | grep RESULT >> results/eval_$sc.csv
      done
    done
    kill $SP
  ) &
done
wait
echo ALLDONE
