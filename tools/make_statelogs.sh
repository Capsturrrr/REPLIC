#!/bin/bash
# Makes /tmp/sl_train.csv and /tmp/sl_test.csv (decision logs of the neural agent) for tools/interpret.py.
# Train = seeds 1..20, test = seeds 424242..424261, over five paths. Results are close to, not identical with, the committed ones.
cd "$(dirname "$0")/.."; make smart >/dev/null 2>&1
PATHS=("--delay 5 --jitter 1 --loss 0.1 --rate 3000 --queue 100" "--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50" "--delay 300 --jitter 20 --loss 0.5 --rate 400 --queue 80" "--delay 10 --jitter 3 --loss 0 --rate 1500 --queue 60" "--delay 40 --jitter 10 --loss 5 --rate 600 --queue 40")
mk() { out=$1; s0=$2; : > $out; for p in "${PATHS[@]}"; do for i in $(seq 0 19); do
  rm -f /tmp/_sl1.csv; REPLIC_STATELOG=/tmp/_sl1.csv ./bin/smart_client --vt 1 --mode deep --dfile results/dqn_chat.txt $p --packets 8000 --seed $((s0+i)) >/dev/null 2>&1; cat /tmp/_sl1.csv >> $out; done; done; wc -l $out; }
mk /tmp/sl_train.csv 1; mk /tmp/sl_test.csv 424242
