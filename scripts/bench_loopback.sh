#!/bin/bash
# Raw-speed check on the loopback interface (no emulated path): how many packets/s and MB/s the C sender sustains
# and that all data is delivered. 4,096 packets per run; reports the median of N runs per policy.
cd "$(dirname "$0")/.."; N=${1:-9}; port=9971
gcc -O2 -DSERVER_PORT=$port -o bin/server_bench src/server.c src/common.c || exit 1
make smart >/dev/null 2>&1
./bin/server_bench >/dev/null 2>&1 & SP=$!; sleep 0.3
PB=$(grep -o "define PKT_BYTES [0-9]*" src/smart_client.c | awk '{print $3}')
for m in aimd cubic deep; do
  for i in $(seq 1 $N); do timeout 60 ./bin/smart_client --mode $m --emu 0 --init 10 --dfile results/dqn_chat.txt --port $port --packets 4096 --seed $i | grep RESULT | awk -F, '{print $4, $6}'; done | sort -n | awk -v m=$m -v pb=${PB:-512} '{t[NR]=$1; s=$2} END{med=t[int((NR+1)/2)]; printf "%-6s median %.1f ms for %d packets: %.0f packets/s, %.1f MB/s (payload %d B)\n", m, med, s, s/(med/1000), s*pb/(med/1000)/1e6, pb}'
done
kill $SP
