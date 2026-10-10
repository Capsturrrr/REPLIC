#!/usr/bin/env python3
"""Fairness and speed of weight files on held-out random paths (virtual time). For each path: (a) one flow alone, (b) the flow against a
backlogged AIMD competitor on the same bottleneck. Reports share of the link, time ratio to tuned AIMD, and the tail.
Usage: vt_fair.py N SEED file1 [file2 ...]   (uses 'polite' suffix: file:polite=1.3)"""
import math, random, sys, os, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vt, vt_random
from concurrent.futures import ThreadPoolExecutor
n = int(sys.argv[1]); seed0 = int(sys.argv[2]); specs = sys.argv[3:]
rng = random.Random(seed0); paths = [vt_random.draw(rng) for _ in range(n)]
def one(i):
    pa, k = paths[i]; out = {}
    for comp in (0, 1):
        ex = "--vcomp 1" if comp else ""
        base = vt.run("aimd_128", pa, 1 + i * 7, npk=k, extra=ex)
        out[("aimd", comp)] = base
        for sp in specs:
            f, _, pol = sp.partition(":polite=")
            r = vt.run("deep", pa, 1 + i * 7, npk=k, dfile=f, extra=ex + (f" --polite {pol}" if pol else ""))
            out[(sp, comp)] = r
    return out
with ThreadPoolExecutor(2) as ex: res = list(ex.map(one, range(n)))
print(f"{n} random held-out paths (seed {seed0}); ratio = time / tuned AIMD in the same situation (lower is better)")
print(f"{'weights':44s} {'alone: ratio':>12s} {'worst':>6s} {'with competitor: ratio':>23s} {'share':>6s} {'share>0.6':>9s}")
for sp in specs:
    a = [r[(sp, 0)]["time"] / r[("aimd", 0)]["time"] for r in res if r[(sp, 0)] and r[("aimd", 0)]]
    c = [r[(sp, 1)]["time"] / r[("aimd", 1)]["time"] for r in res if r[(sp, 1)] and r[("aimd", 1)]]
    sh = [r[(sp, 1)]["share"] for r in res if r[(sp, 1)] and r[(sp, 1)]["share"] is not None]
    g = lambda v: math.exp(st.mean(math.log(x) for x in v))
    print(f"{sp:44s} {g(a):12.3f} {max(a):6.2f} {g(c):23.3f} {st.mean(sh):6.2f} {sum(x > 0.6 for x in sh):6d}/{len(sh)}")
sh = [r[("aimd", 1)]["share"] for r in res if r[("aimd", 1)] and r[("aimd", 1)]["share"] is not None]
print(f"{'(AIMD against the AIMD competitor)':44s} {'':12s} {'':6s} {'':23s} {st.mean(sh):6.2f}")
