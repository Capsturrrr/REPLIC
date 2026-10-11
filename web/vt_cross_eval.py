#!/usr/bin/env python3
"""Held-out evaluation with bursty cross traffic (virtual time): N random paths (seed S), each run alone and with random cross traffic.
Usage: vt_cross_eval.py N SEED weights1 [weights2 ...]"""
import math, random, sys, os, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vt, vt_random
from concurrent.futures import ThreadPoolExecutor
n = int(sys.argv[1]); seed0 = int(sys.argv[2]); files = sys.argv[3:]
rng = random.Random(seed0); paths = [vt_random.draw(rng) for _ in range(n)]
def cross(pa, i):
    r = random.Random(900000 + seed0 + i); rate = float(pa.split("--rate")[1].split()[0])
    return f"--vcross {r.uniform(100, 600):.0f}:{r.uniform(200, 1000):.0f}:{rate * r.uniform(0.3, 0.9):.0f}"
vt.ARMS["cubic_128"] = vt.ARMS["cubic_128"]
def one(i):
    pa, k = paths[i]; out = {}
    for cr in (0, 1):
        ex = cross(pa, i) if cr else ""
        out[("aimd", cr)] = vt.run("aimd_128", pa, 1 + i * 7, npk=k, extra=ex)
        out[("cubic", cr)] = vt.run("cubic_128", pa, 1 + i * 7, npk=k, extra=ex)
        for f in files: out[(f, cr)] = vt.run("deep", pa, 1 + i * 7, npk=k, dfile=f, extra=ex)
    return out
with ThreadPoolExecutor(2) as ex: res = list(ex.map(one, range(n)))
g = lambda v: math.exp(st.mean(math.log(x) for x in v))
print(f"{n} held-out random paths (seed {seed0}); ratio = time / tuned AIMD in the same situation, geometric mean (lower is better)")
print(f"{'policy':36s} {'alone':>7s} {'worst':>6s} {'with cross traffic':>19s} {'worst':>6s} {'faster on':>10s} {'>20% slower':>12s} {'retx/pkt':>9s}")
for name in ["cubic"] + files:
    row = []
    for cr in (0, 1):
        v = [r[(name, cr)]["time"] / r[("aimd", cr)]["time"] for r in res if r[(name, cr)] and r[("aimd", cr)]]
        row.append(v)
    rx = st.mean(r[(name, 1)]["retx"] / paths[i][1] for i, r in enumerate(res) if r[(name, 1)])
    print(f"{name:36s} {g(row[0]):7.3f} {max(row[0]):6.2f} {g(row[1]):19.3f} {max(row[1]):6.2f} {sum(x < 0.95 for x in row[1]):7d}/{len(row[1])} {sum(x > 1.2 for x in row[1]):9d}/{len(row[1])} {rx:9.3f}")
