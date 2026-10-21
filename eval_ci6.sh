#!/bin/bash
# AIMD (cap 32) vs neural agent vs neural agent with an AIMD hand-over when the per-interval loss fraction is high (--hybrid).
# (Go-Back-N era: the transport was Go-Back-N when this was run; the script now pins it with -DREPLIC_GBN and --gbn.)
# usage: bash eval_ci6.sh [runs] [badruns] -> results/eval_ci6_<scenario>.csv ; summarise with python3 ci_summary6.py
export REPLIC_CLASSIC=1   # keep the original agent settings (cap 32, no hand-over) so these results stay reproducible
export REPLIC_CLASSIC=1
cd "$(dirname "$0")"; RUNS=${1:-10}; BADRUNS=${2:-5}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60 --packets 1200"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50 --packets 1200"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100 --packets 1200"
P[bad]="--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40 --packets 300"
port=9900
for sc in 5g lossy sat bad; do
  port=$((port+1)); gcc -DREPLIC_GBN -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  N=$RUNS; [ $sc = bad ] && N=$BADRUNS
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_ci6_$sc.csv
    for i in $(seq 1 $N); do
      for arm in aimd32 deep hyb05 hyb10; do
        case $arm in
          aimd32) A="--mode aimd --init 10 --maxcwnd 32";;
          deep)   A="--mode deep --init 10 --dfile results/dqn_chat.txt";;
          hyb05)  A="--mode deep --init 10 --dfile results/dqn_chat.txt --hybrid 0.05";;
          hyb10)  A="--mode deep --init 10 --dfile results/dqn_chat.txt --hybrid 0.10";;
        esac
        timeout 250 ./bin/smart_client --gbn $A --port $port ${P[$sc]} --scenario $sc --seed $((7000+i)) | grep RESULT | sed "s/RESULT,\([^,]*\),[^,]*,/RESULT,\1,$arm,/" >> results/eval_ci6_$sc.csv
      done
    done; kill $SP ) &
done
wait; echo DONE
