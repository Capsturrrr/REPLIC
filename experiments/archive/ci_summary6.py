#!/usr/bin/env python3
"""Equal-cap comparison (C transport): mean time with 95% t-interval, and paired differences against AIMD cap 32 and cap 128."""
import csv, math, os, statistics as st
T = {1:12.7,2:4.30,3:3.18,4:2.78,5:2.57,6:2.45,7:2.36,8:2.31,9:2.26,10:2.23,11:2.20,12:2.18,13:2.16,14:2.14,15:2.13}
ARMS = ("aimd32", "deep", "hyb05", "hyb10")
for sc in ("5g", "lossy", "sat", "bad"):
    f = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."), "results", f"eval_ci6_{sc}.csv")
    if not os.path.exists(f): continue
    d = {a: [] for a in ARMS}
    for r in csv.reader(open(f)):
        if len(r) > 5 and r[2] in d: d[r[2]].append((float(r[3]), int(r[5])))
    for a in ARMS:
        ts = [x[0] for x in d[a]]; n = len(ts)
        if n < 2: continue
        h = T.get(n - 1, 1.96) * st.stdev(ts) / math.sqrt(n)
        print(f"{sc:6s} {a:8s} n={n:2d} time {st.mean(ts)/1000:6.2f} s +/- {h/1000:.2f}   sent {st.mean(x[1] for x in d[a]):6.0f}")
    for ref in ("aimd32",):
        for a in ("deep", "hyb05", "hyb10"):
            k = min(len(d[ref]), len(d[a]))
            if k >= 2:
                diff = [d[a][i][0] - d[ref][i][0] for i in range(k)]; h = T.get(k - 1, 1.96) * st.stdev(diff) / math.sqrt(k)
                print(f"       paired {a} - {ref}: {st.mean(diff)/1000:+.2f} s +/- {h/1000:.2f} ({'significant' if abs(st.mean(diff)) > h else 'not significant'})")
