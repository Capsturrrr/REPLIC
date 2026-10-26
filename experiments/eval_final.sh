#!/bin/bash
# Final comparison (real clock, real server process, in-process emulated path): original AIMD, tuned AIMD, tuned CUBIC and the neural agent with the final defaults.
cd "$(dirname "$0")/.."; RUNS=${1:-8}; BADRUNS=${2:-5}
declare -A P NP
P[5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"; NP[5g]=1200
P[lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"; NP[lossy]=1200
P[sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"; NP[sat]=1200
P[fast]="--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300"; NP[fast]=3000
P[bad]="--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40"; NP[bad]=300
port=9580
for sc in 5g lossy sat fast bad; do
  port=$((port+1)); gcc -DSERVER_PORT=$port -o bin/server_$port src/server.c src/common.c
  N=$RUNS; [ $sc = bad ] && N=$BADRUNS
  ( ./bin/server_$port >/dev/null 2>&1 & SP=$!; sleep 0.3; rm -f results/eval_final_$sc.csv
    for i in $(seq 1 $N); do
      for arm in aimd_32 aimd_128 cubic_128 deep; do
        case $arm in
          aimd_32) A="--mode aimd --init 10 --maxcwnd 32";;
          aimd_128) A="--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";;
          cubic_128) A="--mode cubic --init 10 --maxcwnd 128 --ssthresh 128";;
          deep) A="--mode deep --init 10 --dfile results/dqn_chat.txt";;
        esac
        REPLIC_QUIET=1 timeout 250 ./bin/smart_client $A --port $port ${P[$sc]} --packets ${NP[$sc]} --scenario $sc --seed $((9900+i)) | grep RESULT | sed "s/RESULT,\([^,]*\),[^,]*,/RESULT,\1,$arm,/" >> results/eval_final_$sc.csv
      done
    done; kill $SP ) &
done
wait; echo DONE
