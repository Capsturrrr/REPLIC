#!/bin/bash
# Performance matrix (Selective Repeat transport): which window policy and cap is fastest?
# usage: bash experiments/archive/eval_perf.sh [runs] -> results/eval_perf_<scenario>.csv ; python3 experiments/archive/ci_summary_perf.py
export REPLIC_CLASSIC=1   # these runs used cap 32 / no hand-over unless a flag says otherwise
cd "$(dirname "$0")/../.."; RUNS=${1:-5}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60 --packets 1200"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50 --packets 1200"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100 --packets 1200"
P[fast]="--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300 --packets 3000"
port=9950
for sc in 5g lossy sat fast; do
  port=$((port+1)); gcc -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_perf_$sc.csv
    for i in $(seq 1 $RUNS); do
      for arm in aimd_old aimd_ss32 aimd_128 cubic_128 deep deep_64; do
        case $arm in
          aimd_old)  A="--mode aimd --init 10 --maxcwnd 32";;
          aimd_ss32) A="--mode aimd --init 10 --maxcwnd 32 --ssthresh 32";;
          aimd_128)  A="--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";;
          cubic_128) A="--mode cubic --init 10 --maxcwnd 128 --ssthresh 128";;
          deep)      A="--mode deep --init 10 --dfile results/dqn_chat.txt";;
          deep_64)   A="--mode deep --init 10 --dfile results/dqn_chat.txt --maxcwnd 64";;
        esac
        timeout 250 ./bin/smart_client $A --port $port ${P[$sc]} --scenario $sc --seed $((9000+i)) | grep RESULT | sed "s/RESULT,\([^,]*\),[^,]*,/RESULT,\1,$arm,/" >> results/eval_perf_$sc.csv
      done
    done; kill $SP ) &
done
wait; echo DONE
