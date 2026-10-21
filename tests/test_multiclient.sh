#!/bin/bash
# Two senders talk to ONE server at once; each file must arrive intact (server keeps one session per client address).
cd "$(dirname "$0")/.."; D=$(mktemp -d); trap 'kill $SP 2>/dev/null; rm -rf $D' EXIT
gcc -DSERVER_PORT=9941 -o $D/srv src/server.c src/common.c || exit 1
head -c 250000 /dev/urandom > $D/a.bin; head -c 150000 /dev/urandom > $D/b.bin
REPLIC_OUT=$D/out.bin REPLIC_QUIET=1 $D/srv > $D/srv.log 2>&1 & SP=$!; sleep 0.3
./bin/smart_client --mode aimd --emu 5 --port 9941 --file $D/a.bin > $D/ca.log 2>&1 &
./bin/smart_client --mode aimd --emu 5 --port 9941 --file $D/b.bin > $D/cb.log 2>&1 &
wait $(jobs -p | grep -v $SP) 2>/dev/null; sleep 0.5
ok=0; for f in $D/out.bin*; do for s in a b; do cmp -s $f $D/$s.bin && ok=$((ok+1)); done; done
[ $ok = 2 ] && echo "ok:   both concurrent files received intact" || { echo "FAIL: concurrent transfers ($ok of 2 intact)"; exit 1; }
