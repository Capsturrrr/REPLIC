#!/usr/bin/env python3
"""Time-varying path test (virtual time). Paired seeds, 95% t-interval on the mean completion time (ms).
usage: tools/dyn_eval.py [N_SEEDS] [arms=aimd,cubic,deep,bbr]"""
import subprocess, sys, statistics as st, math, os
from concurrent.futures import ThreadPoolExecutor
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
ARMS = (sys.argv[2] if len(sys.argv) > 2 else "aimd,cubic,deep").split(",")
BASE = "--vt 1 --delay 30 --jitter 3 --loss 0.2 --rate 1500 --queue 60 --packets 4000"
def leo():
    s = []; t = 1000; d = 25
    while t < 12000:
        s.append(f"{t}:200:{d}"); s.append(f"{t+150}:1500:{d}"); d = 45 if d == 25 else 25; t += 1000
    return ";".join(s)
SC = {
 "bw_drop":  "800:300:0",
 "bw_rise":  "800:1500:0",     # starts at --rate 300 (see override below)
 "rtt_jump": "800:0:120",
 "leo_handover": leo(),
}
OVR = {"bw_rise": "--rate 300"}
ARMD = {"aimd": "--mode aimd --maxcwnd 128 --ssthresh 128", "cubic": "--mode cubic",
        "deep": "--mode deep --dfile results/dqn_chat.txt", "bbr": "--mode bbr"}
def run(sc, arm, seed):
    cmd = f"./bin/smart_client {ARMD[arm]} {BASE} {OVR.get(sc,'')} --dyn '{SC[sc]}' --seed {seed}"
    o = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout
    for l in o.splitlines():
        if l.startswith("RESULT"):
            f = l.split(","); return float(f[3]), int(f[6])
    return float("nan"), 0
def ci(x):
    n = len(x); m = st.mean(x); h = (2.045 if n == 30 else 2.093 if n == 20 else 2.0) * st.stdev(x) / math.sqrt(n)
    return m, h
out = []
with ThreadPoolExecutor(2) as ex:
    for sc in SC:
        res = {}
        for a in ARMS:
            r = list(ex.map(lambda s: run(sc, a, 424242 + s), range(N)))
            res[a] = r
        line = [f"{sc:14s}"]
        for a in ARMS:
            t = [x[0] for x in res[a]]; rt = st.mean(x[1] for x in res[a]); m, h = ci(t)
            line.append(f"{a}: {m/1000:6.2f}s ±{h/1000:4.2f} (retx {rt:5.0f})")
        out.append("  ".join(line)); print(out[-1], flush=True)
open("results/dynamic_path.txt", "w").write("# dynamic path, %d paired seeds (test seed 424242+)\n# base: %s\n" % (N, BASE) + "\n".join(out) + "\n")
