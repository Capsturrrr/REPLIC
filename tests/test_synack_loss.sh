#!/usr/bin/env bash
# Regression test: the first SYN-ACK is lost (a return-path loss the UDP proxy never produces, but netem does).
# The client retransmits its SYN; the server must treat that as a DUPLICATE of the same connection, not a new one.
# usage: bash tests/test_synack_loss.sh [drops]     exit 0 = delivered byte-identical to out.bin
set -u; cd "$(dirname "$0")/.."; N=${1:-1}; W=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$W"' EXIT
CF="-O2 -std=c11 -D_POSIX_C_SOURCE=200809L -D_GNU_SOURCE -w"; mkdir -p bin
gcc $CF -DSERVER_PORT=9961 -o bin/server_drop src/server.c src/common.c && gcc $CF -o bin/smart_client src/smart_client.c src/poise_sender.c src/common.c -lm || exit 2
head -c 200000 /dev/urandom > "$W/in.bin"
POISE_OUT="$W/out.bin" POISE_QUIET=1 bin/server_drop >/dev/null 2>&1 & sleep 0.3
python3 tests/dropsynack.py 9962 9961 "$N" & sleep 0.3
POISE_OUT="$W/out.bin" timeout 60 bin/smart_client --mode aimd --maxcwnd 32 --init 10 --emu 0 --port 9962 --file "$W/in.bin" >/dev/null 2>&1
sleep 1
a=$(sha256sum < "$W/in.bin"); b=$(sha256sum < "$W/out.bin" 2>/dev/null); extra=$(ls "$W" | grep -c 'out.bin\.')
if [ "$a" = "$b" ]; then echo "PASS: byte-identical after $N lost SYN-ACK(s)"; else echo "FAIL: out.bin differs (size $(stat -c%s "$W/out.bin" 2>/dev/null)), extra per-port files: $extra"; exit 1; fi
