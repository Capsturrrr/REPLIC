#!/usr/bin/env python3
"""Speed cost of distillation and robustness to measurement noise (C sender, virtual time, paired seeds, 95% t-interval)."""
import subprocess, os, statistics as st, math, sys
from concurrent.futures import ThreadPoolExecutor
N = 20; T = 2.093
SC = {"5g":"--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60","lossy":"--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50",
 "sat":"--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100","fast":"--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300","bad":"--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40"}
ARMS = {"network":{}, "tree d8":{"RUDP_TREE":"results/interp/tree_d8.txt"}, "tree d5":{"RUDP_TREE":"results/interp/tree_d5.txt"},
        "noise .02":{"RUDP_NOISE":"0.02"}, "noise .05":{"RUDP_NOISE":"0.05"}, "noise .1":{"RUDP_NOISE":"0.1"}, "noise .2":{"RUDP_NOISE":"0.2"}}
def run(sc, env, seed):
    o = subprocess.run(f"./bin/smart_client --mode deep --dfile results/dqn_chat.txt --vt 1 {SC[sc]} --packets 3000 --seed {seed}", shell=True,
                       capture_output=True, text=True, env={**os.environ, **env}).stdout
    for l in o.splitlines():
        if l.startswith("RESULT"): return float(l.split(",")[3]) / 1000
    return 600.0
L = [f"# {N} paired seeds; completion time in seconds, mean ±95% CI (RUDP_TREE = distilled tree, RUDP_NOISE = Gaussian noise on the 4 measured inputs)",
     "scenario  " + "  ".join(f"{a:>13s}" for a in ARMS)]
print(L[1], flush=True)
with ThreadPoolExecutor(2) as ex:
    for sc in SC:
        row = f"{sc:9s}"
        for a, env in ARMS.items():
            x = list(ex.map(lambda s: run(sc, env, 424242 + s), range(N)))
            row += f"  {st.mean(x):6.2f}±{T*st.stdev(x)/math.sqrt(N):5.2f}"
        print(row, flush=True); L.append(row)
open("results/interp/robustness_and_distillation.txt", "w").write("\n".join(L) + "\n")
