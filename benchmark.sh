#!/bin/bash
# Poise Phase 7 — quick benchmark sweep across loss percentages
# Usage: ./benchmark.sh
# Requires: server already running in another terminal (./bin/server)

OUT="logs/benchmark_results.csv"
mkdir -p logs
echo "loss_percent,duration_ms,packets_sent,final_cwnd" > "$OUT"

for loss in 0 5 10 15 20 30 40; do
    echo "=== Testing with ${loss}% loss ==="
    START=$(date +%s.%N)
    RESULT=$(timeout 30 ./bin/client "$loss" 2>&1)
    END=$(date +%s.%N)
    DURATION=$(awk "BEGIN {printf \"%.0f\", ($END - $START) * 1000}")

    FINAL_CWND=$(echo "$RESULT" | grep 'Final cwnd=' | tail -1 | sed 's/.*Final cwnd=\([0-9.]*\).*/\1/')
    PACKETS=$(echo "$RESULT" | grep 'packets delivered' | tail -1 | sed 's/All \([0-9]*\) packets delivered.*/\1/')

    echo "${loss},${DURATION},${PACKETS:-0},${FINAL_CWND:-0}" >> "$OUT"
    echo "  Duration: ${DURATION}ms, Packets: ${PACKETS}, Final cwnd: ${FINAL_CWND}"
    sleep 1
done

echo ""
echo "Benchmark complete. Results in $OUT"
cat "$OUT"
