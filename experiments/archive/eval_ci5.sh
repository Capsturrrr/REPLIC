#!/bin/bash
# AIMD (cap 32) vs the neural agent with and without the timeout safety net (--tcut 0.25), plus a hostile 10%-loss scenario.
# (Go-Back-N era: the transport was Go-Back-N when this was run; the script now pins it with -DREPLIC_GBN and --gbn.)
# usage: bash experiments/archive/eval_ci5.sh [runs] -> results/eval_ci5_<scenario>.csv ; summarise with python3 experiments/archive/ci_summary5.py
export REPLIC_CLASSIC=1   # keep the original agent settings (cap 32, no hand-over) so these results stay reproducible
export REPLIC_CLASSIC=1
cd "$(dirname "$0")/../.."; RUNS=${1:-10}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"
P[bad]="--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40"
port=9800
for sc in 5g lossy sat bad; do
  port=$((port+1)); gcc -DREPLIC_GBN -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_ci5_$sc.csv
    for i in $(seq 1 $RUNS); do
      for arm in aimd32 deep deepcut; do
        case $arm in
          aimd32)  A="--mode aimd --init 10 --maxcwnd 32";;
          deep)    A="--mode deep --init 10 --dfile results/dqn_chat.txt";;
          deepcut) A="--mode deep --init 10 --dfile results/dqn_chat.txt --tcut 0.25";;
        esac
        ./bin/smart_client --gbn $A --port $port ${P[$sc]} --scenario $sc --packets 1200 --seed $((6000+i)) | grep RESULT | sed "s/RESULT,\([^,]*\),[^,]*,/RESULT,\1,$arm,/" >> results/eval_ci5_$sc.csv
      done
    done; kill $SP ) &
done
wait; echo DONE
