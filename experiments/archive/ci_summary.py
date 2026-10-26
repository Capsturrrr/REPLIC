#!/usr/bin/env python3
"""Mean and 95% confidence interval (t-based) of completion time per scenario and mode, from results/eval_ci_*.csv."""
import csv, glob, math, os, statistics as st
T = {1:12.7,2:4.30,3:3.18,4:2.78,5:2.57,6:2.45,7:2.36,8:2.31,9:2.26,10:2.23,11:2.20,12:2.18,13:2.16,14:2.14,15:2.13,16:2.12,17:2.11,18:2.10,19:2.09,20:2.09}
for sc in ("5g", "lossy", "sat"):
    f = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."), "results", f"eval_ci_{sc}.csv")
    if not os.path.exists(f): continue
    d = {"aimd": [], "rl": []}
    for r in csv.reader(open(f)):
        if len(r) > 5 and r[2] in d: d[r[2]].append((float(r[3]), int(r[5])))
    for m in ("aimd", "rl"):
        ts = [x[0] for x in d[m]]; rt = [x[1] for x in d[m]]; n = len(ts)
        if n < 2: continue
        h = T.get(n - 1, 1.96) * st.stdev(ts) / math.sqrt(n)
        print(f"{sc:6s} {m:5s} n={n:2d} time {st.mean(ts)/1000:6.2f} s ± {h/1000:.2f} (95% CI)   sent {st.mean(rt):6.0f}")
    a = [x[0] for x in d["aimd"]]; b = [x[0] for x in d["rl"]]
    k = min(len(a), len(b))
    if k >= 2:
        diff = [y - x for x, y in zip(a[:k], b[:k])]; h = T.get(k - 1, 1.96) * st.stdev(diff) / math.sqrt(k)
        print(f"       paired difference Smart-AIMD: {st.mean(diff)/1000:+.2f} s ± {h/1000:.2f}  ({'significant' if abs(st.mean(diff)) > h else 'not significant'})")
