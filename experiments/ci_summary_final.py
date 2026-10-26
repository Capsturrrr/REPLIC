#!/usr/bin/env python3
"""Mean +/- 95% interval per arm and scenario from results/eval_final_*.csv, and the fastest arm."""
import csv, math, os, statistics as st
T = {1:12.7,2:4.30,3:3.18,4:2.78,5:2.57,6:2.45,7:2.36,8:2.31,9:2.26,10:2.23}
for sc in ("5g", "lossy", "sat", "fast", "bad"):
    f = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), "results", f"eval_final_{sc}.csv")
    if not os.path.exists(f): continue
    d = {}
    for r in csv.reader(open(f)):
        if len(r) > 5: d.setdefault(r[2], []).append((float(r[3]) / 1000, int(r[5]), int(r[6])))
    best = min(d, key=lambda a: st.mean(x[0] for x in d[a]))
    for a, v in d.items():
        ts = [x[0] for x in v]; n = len(ts)
        h = T.get(n - 1, 1.96) * st.stdev(ts) / math.sqrt(n) if n > 1 else 0
        print(f"{sc:6s} {a:10s} n={n:2d} {st.mean(ts):7.2f} s +/- {h:5.2f}  sent {st.mean(x[1] for x in v):6.0f} retx {st.mean(x[2] for x in v):5.0f}{'   <- fastest' if a == best else ''}")
# paired differences against the original baseline and the tuned baseline
for sc in ("5g", "lossy", "sat", "fast", "bad"):
    f = os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."), "results", f"eval_final_{sc}.csv")
    if not os.path.exists(f): continue
    d = {}
    for r in csv.reader(open(f)):
        if len(r) > 5: d.setdefault(r[2], []).append(float(r[3]) / 1000)
    for ref in ("aimd_32", "cubic_128", "aimd_128"):
        k = min(len(d.get(ref, [])), len(d.get("deep", [])))
        if k >= 2:
            diff = [d["deep"][i] - d[ref][i] for i in range(k)]; h = T.get(k - 1, 1.96) * st.stdev(diff) / math.sqrt(k)
            print(f"       {sc}: deep - {ref}: {st.mean(diff):+.2f} s +/- {h:.2f} ({'significant' if abs(st.mean(diff)) > h else 'not significant'}), {100*st.mean(diff)/st.mean(d[ref]):+.0f}%")
