#!/bin/bash
# File transfer over REPLIC through an impaired network (real UDP sockets, independent impairment process).
#   ./filetransfer_demo.sh [size_MB] [profile]      profiles: 5g lossy sat fast bad   (default: 2 MB over "lossy")
# Sends a random file with AIMD (cap 32), tuned AIMD (cap 128) and the neural agent, then checks that the received
# file is byte-identical (FNV-1a hash of sender and receiver) and prints the goodput.
cd "$(dirname "$0")"; MB=${1:-2}; PROF=${2:-lossy}
declare -A P=( [5g]="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60" [lossy]="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50" \
  [sat]="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100" [fast]="--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300" [bad]="--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40" )
[ -z "${P[$PROF]}" ] && { echo "unknown profile $PROF (5g lossy sat fast bad)"; exit 1; }
make smart impair >/dev/null 2>&1
SP_PORT=9941; PX_PORT=9942; IN=$(mktemp); OUT=$(mktemp)
head -c $((MB*1000000)) /dev/urandom > $IN
gcc -O2 -DSERVER_PORT=$SP_PORT -o bin/server_ft src/server.c src/common.c
echo "sending ${MB} MB over the '$PROF' path (${P[$PROF]})"
printf "%-26s %9s %10s %8s   %s\n" policy "time" "goodput" "retx" integrity
for arm in aimd_32 aimd_128 deep; do
  case $arm in aimd_32) A="--mode aimd --init 10 --maxcwnd 32";; aimd_128) A="--mode aimd --init 10 --maxcwnd 128 --ssthresh 128";; deep) A="--mode deep --init 10 --dfile results/dqn_chat.txt";; esac
  REPLIC_OUT=$OUT REPLIC_QUIET=1 ./bin/server_ft > /tmp/ft_srv.log 2>&1 & SP=$!
  ./bin/impair $PX_PORT $SP_PORT ${P[$PROF]} >/dev/null 2>&1 & PX=$!; sleep 0.3
  R=$(timeout 600 ./bin/smart_client $A --emu 0 --port $PX_PORT --file $IN)
  sleep 0.4; kill $PX $SP 2>/dev/null; wait $PX $SP 2>/dev/null
  S=$(echo "$R" | grep ^FILE | sed 's/.*fnv1a=\([0-9a-f]*\).*/\1/'); D=$(grep TRANSFER /tmp/ft_srv.log | sed 's/.*fnv1a=//')
  T=$(echo "$R" | grep RESULT | cut -d, -f4); X=$(echo "$R" | grep RESULT | cut -d, -f7)
  [ "$S" = "$D" ] && [ -n "$S" ] && OK="identical ($S)" || OK="MISMATCH"
  printf "%-26s %7.2f s %6.2f MB/s %8s   %s\n" "$arm" $(echo "$T/1000" | bc -l) $(echo "$MB*1000/$T" | bc -l) "$X" "$OK"
done
rm -f $IN $OUT
