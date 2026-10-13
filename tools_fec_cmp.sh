#!/bin/bash
D=/home/claude/poise2; cd $D; head -c 256000 /dev/urandom > /tmp/pl.bin
CF="-O2 -std=c11 -D_POSIX_C_SOURCE=200809L -D_GNU_SOURCE -w"
gcc $CF -DSERVER_PORT=9961 -o bin/server_drop src/server.c src/common.c; gcc $CF -o bin/smart_client src/smart_client.c src/poise_sender.c src/common.c -lm; gcc $CF -o bin/impair src/impair.c -lm
LOSS=${LOSS:-2}; RATE=${RATE:-346}; Q=${Q:-22}; DELAY=${DELAY:-25}
for i in 1 2 3 4; do for K in 0 8 4; do for m in aimd deep; do
 case $m in aimd) a="--mode aimd --maxcwnd 128 --ssthresh 128 --init 10";; deep) a="--mode deep --init 10 --dfile results/dqn_chat.txt";; esac
 rm -f /tmp/o.bin; POISE_OUT=/tmp/o.bin POISE_QUIET=1 ./bin/server_drop >/dev/null 2>&1 & S=$!
 ./bin/impair 9962 9961 --delay $DELAY --jitter 10 --loss $LOSS --rate $RATE --queue $Q >/dev/null 2>&1 & P=$!; sleep .3
 r=$(POISE_FEC=$K timeout 40 ./bin/smart_client $a --emu 0 --port 9962 --file /tmp/pl.bin 2>&1 | grep RESULT | cut -d, -f3-6)
 sleep .2; ok=$(cmp -s /tmp/o.bin /tmp/pl.bin && echo OK || echo BAD)
 echo "K=$K $m $r $ok"
 kill $S $P 2>/dev/null; wait $S $P 2>/dev/null
done; done; done
