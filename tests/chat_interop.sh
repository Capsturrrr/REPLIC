#!/bin/bash
# Interop test: the Python web gateway and the C terminal chat speak the same REPLIC (256-packet Selective Repeat window,
# 32-byte ACK bitmap, CRC-16) and deliver 100 messages each way, in order, through 20% loss on both sides.
cd "$(dirname "$0")/.."; make chat >/dev/null 2>&1
rm -f /tmp/ci_chat.out; mkfifo /tmp/ci_chat.in 2>/dev/null
python3 web/replic_web.py --udp 7301 --peer 127.0.0.1:7302 --http 8391 --loss 20 --name gw >/tmp/ci_gw.log 2>&1 & GW=$!
( exec 3<>/tmp/ci_chat.in; exec stdbuf -oL ./bin/chat 7302 7301 cc 20 <&3 > /tmp/ci_chat.out 2>&1 ) & CH=$!
sleep 1.5
for i in $(seq 1 100); do curl -s -X POST -d "{\"text\":\"gw-msg-$i\"}" localhost:8391/send >/dev/null; done
for i in $(seq 1 100); do echo "cc-msg-$i" > /tmp/ci_chat.in; done
for t in $(seq 1 ${CHAT_WAIT:-60}); do
  A=$(grep -c "gw-msg-" /tmp/ci_chat.out); B=$(curl -s localhost:8391/state | python3 -c "import sys,json; print(sum(1 for m in json.load(sys.stdin)['msgs'] if 'cc-msg-' in m.get('text','')))")
  [ "$A" -ge 100 ] && [ "$B" -ge 100 ] && break; sleep 1; [ -n "$V" ] && echo "t=$t A=$A B=$B"
done
ORD=$(grep -o "gw-msg-[0-9]*" /tmp/ci_chat.out | awk -F- '{print $3}' | awk 'NR>1 && $1<=p {bad=1} {p=$1} END{print bad?"bad":"ok"}')
[ -n "$V" ] && curl -s localhost:8391/state | python3 -c "import sys,json; d=json.load(sys.stdin); print({k:d[k] for k in d if k not in (\"msgs\",\"log\",\"hist\")}); print(d[\"log\"][-8:])"
kill $GW $CH 2>/dev/null; wait 2>/dev/null; rm -f /tmp/ci_chat.in
echo "terminal chat received $A/100 from gateway (order $ORD), gateway received $B/100 from terminal chat"
[ "$A" -ge 100 ] && [ "$B" -ge 100 ] && [ "$ORD" = ok ] && echo "ok:   gateway and terminal chat interoperate" || { echo "FAIL: chat interop"; exit 1; }
