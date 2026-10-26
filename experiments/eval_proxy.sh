#!/bin/bash
# Cross-check on real sockets: the same five paths, but impaired by an independent process (bin/impair) instead of the
# emulator inside smart_client (--emu 0). Arms: AIMD cap 32, AIMD tuned (cap 128), neural default.
cd "$(dirname "$0")/.."; RUNS=${1:-8}; BADRUNS=${2:-5}; make smart impair >/dev/null 2>&1
declare -A P
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"; N5g=1200
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"
P[fast]="--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300"
P[bad]="--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40"
declare -A NP=( [5g]=1200 [lossy]=1200 [sat]=1200 [fast]=3000 [bad]=300 )
port=9700
for sc in ${SCEN:-5g lossy sat fast bad}; do
  port=$((port+10)); sp=$port; px=$((port+1)); gcc -DSERVER_PORT=$sp -o bin/server_$sp src/server.c src/common.c
  N=$RUNS; [ $sc = bad ] && N=$BADRUNS
  ( ./bin/server_$sp >/dev/null 2>&1 & SP=$!
    rm -f results/eval_proxy_$sc.csv
    for i in $(seq 1 $N); do
      for arm in aimd_32 aimd_128 deep; do
        case $arm in
          aimd_32) A="--mode aimd --init 10 --maxcwnd 32";;
          aimd_128) A="--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";;
          deep) A="--mode deep --init 10 --dfile results/dqn_chat.txt";;
        esac
        ./bin/impair $px $sp ${P[$sc]} --seed $i 2>/dev/null & PX=$!; sleep 0.2
        timeout 250 ./bin/smart_client $A --emu 0 --port $px --packets ${NP[$sc]} --scenario $sc --seed $((9500+i)) | grep RESULT | sed "s/RESULT,\([^,]*\),[^,]*,/RESULT,\1,$arm,/" >> results/eval_proxy_$sc.csv
        kill $PX 2>/dev/null; wait $PX 2>/dev/null
      done
    done; kill $SP ) &
done
wait; echo DONE
