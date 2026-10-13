#!/bin/bash
# Window cap for the neural agent (Selective Repeat transport): 64 / 128 / 250 on high-BDP paths, plus the hostile 10% loss path.
export POISE_CLASSIC=1   # these runs used cap 32 / no hand-over unless a flag says otherwise
cd "$(dirname "$0")"; RUNS=${1:-5}
declare -A P
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100 --packets 1200"
P[fast]="--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300 --packets 3000"
P[bad]="--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40 --packets 300"
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50 --packets 1200"
port=9970
for sc in sat fast bad lossy; do
  port=$((port+1)); gcc -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_perf2_$sc.csv
    for i in $(seq 1 $RUNS); do
      for arm in aimd_128 deep_64 deep_128 deep_250 hyb_128; do
        case $arm in
          aimd_128) A="--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";;
          deep_64)  A="--mode deep --init 10 --dfile results/dqn_chat.txt --maxcwnd 64";;
          deep_128) A="--mode deep --init 10 --dfile results/dqn_chat.txt --maxcwnd 128";;
          deep_250) A="--mode deep --init 10 --dfile results/dqn_chat.txt --maxcwnd 250";;
          hyb_128)  A="--mode deep --init 10 --dfile results/dqn_chat.txt --maxcwnd 128 --hybrid 0.10";;
        esac
        timeout 250 ./bin/smart_client $A --port $port ${P[$sc]} --scenario $sc --seed $((9500+i)) | grep RESULT | sed "s/RESULT,\([^,]*\),[^,]*,/RESULT,\1,$arm,/" >> results/eval_perf2_$sc.csv
      done
    done; kill $SP ) &
done
wait; echo DONE
