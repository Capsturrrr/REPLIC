#!/bin/bash
# Run on the SERVER machine (the far end). Needs: make smart. Open UDP 8888 and TCP 9871 if there is a firewall.
cd "$(dirname "$0")"; make smart >/dev/null 2>&1
echo "Starting REPLIC server (UDP 8888) and TCP sink (TCP 9871). Ctrl+C to stop."
trap 'kill $A $B 2>/dev/null' EXIT
REPLIC_QUIET=1 ./bin/server >/dev/null 2>&1 & A=$!
python3 web/tcp_sink.py 9871 & B=$!
wait
