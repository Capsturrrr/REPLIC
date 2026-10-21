#!/usr/bin/env python3
"""Ablation in the real C sender (virtual time) on 100 random held-out paths (seed 424242, not used for tuning).
Each row switches off one ingredient of the final neural-agent configuration; ratio = completion time / tuned AIMD (cap 128), lower is better."""
import math, os, random, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vt, vt_random
from concurrent.futures import ThreadPoolExecutor

N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
rng = random.Random(424242); paths = [vt_random.draw(rng) for _ in range(N)]
VARIANTS = [   # name, arm, extra flags, env
    ("tuned AIMD (reference)", "aimd_128", "", None),
    ("tuned AIMD, retransmission-timer floor off", "aimd_128", "", {"REPLIC_RTOMULT": "1.0"}),
    ("neural agent, final configuration", "deep", "", None),
    ("  without slow start before the policy", "deep", "--ssdeep 0", None),
    ("  without pacing", "deep", "--pace -1", None),
    ("  without the AIMD hand-over", "deep", "--hybrid 0", None),
    ("  window cap 32 instead of 128", "deep", "--maxcwnd 32", None),
    ("  without the timer floor", "deep", "", {"REPLIC_RTOMULT": "1.0"}),
    ("  original settings (REPLIC_CLASSIC=1)", "deep", "", {"REPLIC_CLASSIC": "1"}),
]
def one(args):
    arm, extra, env, i = args; pa, n = paths[i]
    r = [vt.run(arm, pa, 5 + i * 7 + k, npk=n, extra=extra, env=env) for k in range(2)]
    r = [x for x in r if x]
    return (st.mean(x["time"] for x in r), st.mean(x["retx"] for x in r) / n) if r else (float("nan"), float("nan"))
res = {}
with ThreadPoolExecutor(os.cpu_count() or 2) as ex:
    for name, arm, extra, env in VARIANTS:
        res[name] = list(ex.map(one, [(arm, extra, env, i) for i in range(N)]))
base = res[VARIANTS[0][0]]; full = res["neural agent, final configuration"]
print(f"{'variant':46s} {'ratio to AIMD':>13s} {'vs final':>9s} {'>20% slower':>12s} {'retx/pkt':>9s}")
for name, *_ in VARIANTS:
    rat = [res[name][i][0] / base[i][0] for i in range(N)]
    g = math.exp(st.mean(math.log(x) for x in rat)); gf = math.exp(st.mean(math.log(res[name][i][0] / full[i][0]) for i in range(N)))
    print(f"{name:46s} {g:13.3f} {gf:9.3f} {sum(x > 1.2 for x in rat):9d}/{N} {st.mean(res[name][i][1] for i in range(N)):9.3f}")
