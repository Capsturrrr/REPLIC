#!/bin/bash
# Full test run: wire format, end-to-end delivery over real UDP (clean and lossy), Poise-RUDP modes, Selective Repeat (default) and Go-Back-N, simulator.
cd "$(dirname "$0")/.."; fail=0
step() { echo; echo "== $1"; }
step "build"; make all smart gbn chat >/dev/null 2>&1 && echo ok || { echo "build FAILED"; exit 1; }
step "wire format"; gcc -Wall -o bin/test_rudp tests/test_rudp.c src/common.c && ./bin/test_rudp || fail=1
step "end to end: client and server, 0% and 20% simulated loss"
for L in 0 20; do
  ./bin/server >/tmp/t_srv.log 2>&1 & SP=$!; sleep 0.4
  OUT=$(timeout 120 ./bin/client $L 2>&1 | tail -4)
  kill $SP 2>/dev/null; wait $SP 2>/dev/null
  echo "$OUT" | grep -q "All 62 packets delivered" && echo "ok:   62/62 packets delivered at $L% loss" || { echo "FAIL: delivery at $L% loss"; echo "$OUT"; fail=1; }
done
step "end to end with the original client in Go-Back-N mode (RUDP_GBN=1, server built with -DRUDP_GBN), 20% loss"
./bin/server_gbn >/tmp/t_srv.log 2>&1 & SP=$!; sleep 0.4
OUT=$(RUDP_GBN=1 timeout 120 ./bin/client 20 2>&1 | tail -4); kill $SP 2>/dev/null; wait $SP 2>/dev/null
echo "$OUT" | grep -q "All 62 packets delivered" && echo "ok:   62/62 packets delivered (Go-Back-N, 20% loss)" || { echo "FAIL: GBN delivery"; fail=1; }
step "Poise-RUDP transport modes complete all packets (lossy emulated path)"
for variant in "sr:./bin/server" "gbn:./bin/server_gbn"; do
  name=${variant%%:*}; srv=${variant##*:}; port=$((9600 + RANDOM % 300))
  gcc -DSERVER_PORT=$port $( [ $name = gbn ] && echo -DRUDP_GBN ) -o bin/server_t src/server.c src/common.c
  ./bin/server_t >/dev/null 2>&1 & SP=$!; sleep 0.3
  for m in aimd rl2 deep; do
    R=$(timeout 120 ./bin/smart_client --mode $m --port $port --delay 20 --jitter 5 --loss 3 --rate 800 --queue 50 --packets 300 --qfile results/q_chat.txt --dfile results/dqn_chat.txt --sack $([ $name = sr ] && echo 1 || echo 0) --seed 2 | grep RESULT)
    [ -n "$R" ] && echo "ok:   $m with $name server finished: $(echo $R | cut -d, -f4) ms" || { echo "FAIL: $m with $name server"; fail=1; }
  done
  kill $SP 2>/dev/null; wait $SP 2>/dev/null
done
step "handshake with a lost final ACK"; python3 tests/test_handshake.py || fail=1
step "file transfer through the impairment proxy (real sockets, 5% loss, 25 ms delay): received file is byte-identical"
gcc -DSERVER_PORT=9931 -o bin/server_t2 src/server.c src/common.c; make impair >/dev/null 2>&1
head -c 400000 /dev/urandom > /tmp/t_in.bin
RUDP_OUT=/tmp/t_out.bin RUDP_QUIET=1 ./bin/server_t2 >/tmp/t_srv2.log 2>&1 & SP=$!; ./bin/impair 9932 9931 --delay 25 --jitter 5 --loss 5 --rate 2000 --queue 60 2>/dev/null & PX=$!; sleep 0.4
timeout 120 ./bin/smart_client --mode deep --init 10 --dfile results/dqn_chat.txt --emu 0 --port 9932 --file /tmp/t_in.bin >/tmp/t_cli.log 2>&1; sleep 0.4; kill $PX $SP 2>/dev/null; wait 2>/dev/null
cmp -s /tmp/t_in.bin /tmp/t_out.bin && echo "ok:   400 kB received intact through 5% loss ($(grep -c . /tmp/t_cli.log) lines of client output)" || { echo "FAIL: file transfer corrupted or incomplete"; fail=1; }
step "two clients at the same time on one server (separate sessions, both files byte-identical)"
bash tests/test_multiclient.sh || fail=1
step "librudp public API (buffer transfers, argument checks, state reset between calls)"
bash tests/test_api.sh || fail=1
step "web gateway <-> terminal chat interop (256-packet SR window, 32-byte bitmap, CRC-16), 20% loss both ways"; bash tests/chat_interop.sh || fail=1
step "simulator and controllers"; python3 tests/test_sim.py || fail=1
echo; [ $fail = 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"; exit $fail
