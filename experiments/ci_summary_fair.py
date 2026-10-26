#!/usr/bin/env python3
"""Bandwidth share and Jain index per flow pair from results/eval_fair.csv."""
import csv, statistics as st, collections
d = collections.defaultdict(list)
for r in csv.reader(open("results/eval_fair.csv")):
    try: d[(r[0], r[1])].append((float(r[3]), float(r[4])))
    except: pass
print(f"{'flow A':10s} {'flow B':10s} {'share A':>8s} {'share B':>8s} {'Jain':>6s}  n")
for (A, B), v in d.items():
    sa = st.mean(a / (a + b) for a, b in v); sb = 1 - sa
    j = st.mean((a + b) ** 2 / (2 * (a * a + b * b)) for a, b in v)
    print(f"{A:10s} {B:10s} {100*sa:7.0f}% {100*sb:7.0f}% {j:6.3f}  {len(v)}")
