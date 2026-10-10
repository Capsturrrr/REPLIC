#!/bin/bash
# One-command reproduction of the headline result in virtual time (a few minutes on 2 cores):
#   - builds everything, runs the test suite (skip with NOTEST=1)
#   - five named paths, SEEDS paired seeds each (default 100, as in the report; fewer seeds give noisy verdicts)
#   - 100 random held-out paths
cd "$(dirname "$0")"; SEEDS=${SEEDS:-100}
make all smart chat impair >/dev/null 2>&1 || { echo "build failed"; exit 1; }
[ -z "$NOTEST" ] && { make test 2>&1 | tail -1; }
python3 - <<P
import os, sys, statistics as st
sys.path.insert(0, "web"); import vt
seeds = range(20001, 20001 + $SEEDS); arms = ["aimd_128", "cubic_128", "deep"]
print("\nNamed paths, %d paired seeds, mean completion time in seconds (95%% interval); last column: neural vs tuned AIMD" % len(seeds))
for path in vt.PATHS:
    runs = {a: vt.sweep(a, path, seeds) for a in arms}
    cells = []
    for a in arms:
        m, h = vt.mean_ci([r["time"] for r in runs[a]]); cells.append(f"{m:7.2f}+-{h:4.2f}")
    d = [x["time"] - y["time"] for x, y in zip(runs["deep"], runs["aimd_128"])]; m, h = vt.mean_ci(d)
    pct = 100 * m / st.mean(r["time"] for r in runs["aimd_128"])
    print(f"{path:6s} AIMD {cells[0]}  CUBIC {cells[1]}  neural {cells[2]}   {pct:+4.0f}% ({'significant' if abs(m) > h else 'not significant'})", flush=True)
P
echo; python3 web/vt_random.py 100 424242 | tail -4
