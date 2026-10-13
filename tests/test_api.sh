#!/bin/bash
# libpoise: two consecutive transfers from one process through the public API; the server's hash must match the sender's, and the file must be intact.
cd "$(dirname "$0")/.."; D=$(mktemp -d); trap 'kill $SP 2>/dev/null; rm -rf $D' EXIT
make lib bin/test_api >/dev/null 2>&1 || { echo "FAIL: library build"; exit 1; }
gcc -DSERVER_PORT=9961 -o $D/srv src/server.c src/common.c || exit 1
POISE_OUT=$D/out.bin POISE_QUIET=1 $D/srv > $D/srv.log 2>&1 & SP=$!; sleep 0.3
./bin/test_api 9961 > $D/api.log 2>&1; rc=$?; sleep 0.5
cat $D/api.log | grep -v HASH
a=$(grep "^HASH 0" $D/api.log | cut -d' ' -f3); b=$(grep "^HASH 1" $D/api.log | cut -d' ' -f3)
s=$(grep -o "fnv1a=[0-9a-f]*" $D/srv.log | cut -d= -f2 | tr '\n' ' ')
[ $rc = 0 ] && [ "$s" = "$a $b " ] && echo "ok:   two API transfers (aimd, then cubic) verified by the server's hash ($a)" || { echo "FAIL: API test rc=$rc sender=$a $b server=$s"; exit 1; }
