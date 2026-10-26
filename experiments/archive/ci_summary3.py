#!/usr/bin/env python3
"""Mean and 95% confidence interval (t-based) of completion time per scenario and mode, from results/eval_ci3_*.csv."""
import csv, glob, math, os, statistics as st
T = {1:12.7,2:4.30,3:3.18,4:2.78,5:2.57,6:2.45,7:2.36,8:2.31,9:2.26,10:2.23,11:2.20,12:2.18,13:2.16,14:2.14,15:2.13,16:2.12,17:2.11,18:2.10,19:2.09,20:2.09}
import csv
T.update({21:2.08})
for sc in ("5g", "lossy", "sat", "reorder"):
    f = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."), "results", f"eval_ci3_{sc}.csv")
    if not os.path.exists(f): continue
    d = {k: [] for k in ("aimd-plain", "aimd-sack1", "deep-plain", "deep-sack1")}
    fb = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."), "results", f"eval_ci3b_{sc}.csv")
    rows = list(csv.reader(open(f))) + (list(csv.reader(open(fb))) if os.path.exists(fb) else [])
    for r in rows:
        if len(r) > 5 and r[2] in d: d[r[2]].append((float(r[3]), int(r[5]), int(r[6])))
    for m in d:
        ts = [x[0] for x in d[m]]; n = len(ts)
        if n < 2: continue
        h = T.get(n - 1, 1.96) * st.stdev(ts) / math.sqrt(n)
        print(f"{sc:8s} {m:11s} n={n:2d} time {st.mean(ts)/1000:6.2f} s +/- {h/1000:.2f}   retransmitted {st.mean(x[2] for x in d[m]):6.0f}")
    for m in ("aimd", "deep"):
        k = min(len(d[m+"-plain"]), len(d[m+"-sack1"]))
        if k >= 2:
            diff = [d[m+"-sack1"][i][0] - d[m+"-plain"][i][0] for i in range(k)]; h = T.get(k - 1, 1.96) * st.stdev(diff) / math.sqrt(k)
            print(f"         paired {m} SACK - Go-Back-N (plain server): {st.mean(diff)/1000:+.2f} s +/- {h/1000:.2f} ({'significant' if abs(st.mean(diff)) > h else 'not significant'})")
