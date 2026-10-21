#!/bin/bash
# Equal-window-cap comparison in the C transport: AIMD (cap 128, as originally), AIMD (cap 32, same as the agents),
# the first neural agent (trained in the permissive simulator) and the retrained one (realistic fast-retransmit rule).
# (Go-Back-N era: the transport was Go-Back-N when this was run; the script now pins it with -DREPLIC_GBN and --gbn.)
# usage: bash eval_ci4.sh [runs] -> results/eval_ci4_<scenario>.csv ; summarise with python3 ci_summary4.py
export REPLIC_CLASSIC=1   # keep the original agent settings (cap 32, no hand-over) so these results stay reproducible
export REPLIC_CLASSIC=1
cd "$(dirname "$0")"; RUNS=${1:-10}
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"
port=9700
for sc in 5g lossy sat; do
  port=$((port+1)); gcc -DREPLIC_GBN -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_ci4_$sc.csv
    for i in $(seq 1 $RUNS); do
      for arm in aimd128 aimd32 deep deeprg; do
        case $arm in
          aimd128) A="--mode aimd --init 10";;
          aimd32)  A="--mode aimd --init 10 --maxcwnd 32";;
          deep)    A="--mode deep --init 10 --dfile results/dqn_chat.txt";;
          deeprg)  A="--mode deep --init 10 --dfile results/dqn_chat_rg.txt";;
        esac
        ./bin/smart_client --gbn $A --port $port ${P[$sc]} --scenario $sc --packets 1200 --seed $((5000+i)) | grep RESULT | sed "s/RESULT,\([^,]*\),[^,]*,/RESULT,\1,$arm,/" >> results/eval_ci4_$sc.csv
      done
    done; kill $SP ) &
done
wait; echo DONE
