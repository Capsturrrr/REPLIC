#!/usr/bin/env bash
# Control-flow test of execute_wsl_benchmarks.sh WITHOUT a real tc: stub tc/ip/ethtool/modprobe record their calls and succeed.
# It proves preflight, the sequence of steps, the cleanup trap (also on SIGINT) and packaging. The numbers it produces are NOT netem
# measurements (the loopback is unimpaired) and must never be used.   usage: sudo bash tests/test_wsl_script.sh [sigint]   (sigint mode sends SIGTERM mid-run: bash ignores SIGINT for background jobs)
set -u; HERE="$(cd "$(dirname "$0")/.." && pwd)"; STUB="$(mktemp -d)"; LOGF="$STUB/calls.log"; OUT="$(mktemp -d)/wslt"
for t in tc ip ethtool modprobe; do
  printf '#!/bin/sh\necho "%s $*" >> "%s"\n' "$t" "$LOGF" > "$STUB/$t"
  [ $t = ethtool ] && printf '#!/bin/sh\necho "ethtool $*" >> "%s"\nif [ "$1" = "-k" ]; then printf "tcp-segmentation-offload: on\\ngeneric-segmentation-offload: on\\ngeneric-receive-offload: on\\n"; fi\n' "$LOGF" > "$STUB/$t"
  chmod +x "$STUB/$t"
done
export PATH="$STUB:$PATH"
if [ "${1:-}" = sigint ]; then
  bash "$HERE/execute_wsl_benchmarks.sh" --quick --skip-matrix --out "$OUT" > "$STUB/out.txt" 2>&1 & P=$!
  sleep 12; kill -TERM $P; wait $P; echo "exit status after SIGTERM: $?"
else
  bash "$HERE/execute_wsl_benchmarks.sh" --quick --out "$OUT" > "$STUB/out.txt" 2>&1; echo "exit status: $?"
fi
tail -25 "$STUB/out.txt"; echo "--- tc/ip calls (first 12, last 6)"; head -12 "$LOGF"; echo ...; tail -6 "$LOGF"
echo "--- last tc call must be a qdisc del:"; grep '^tc ' "$LOGF" | tail -1
ls "$OUT" "$OUT/tex" 2>/dev/null; echo "stub dir: $STUB  out: $OUT"
