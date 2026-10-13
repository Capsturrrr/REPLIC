#!/usr/bin/env python3
"""Stress test: many random paths (loss, delay, jitter/reordering, bottleneck, burst size) never used in training."""
import os, random, statistics as st, sys
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc import Controller, load_q
from sim import run
from dqn import DQN
DEEP = os.environ.get("POISE_DEEP") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "dqn_chat.json")
MODES = ("fixed", "aimd", "smart", "deep")

def sample_path(rng):
    return dict(loss=rng.choice([0, 0.005, 0.02, 0.04, 0.07, 0.1, 0.13, 0.18, 0.25, 0.3]),
                delay=rng.choice([0.01, 0.03, 0.06, 0.09, 0.12, 0.2, 0.3]),
                jitter=rng.choice([0, 0, 0.01, 0.03, 0.08]),
                rate=rng.choice([None, None, 60, 150, 400]), queue=rng.choice([5, 10, 20, 40]),
                n=rng.choice([10, 30, 60, 120]))

def one(args):
    qpath, p = args
    q = load_q(qpath)
    means = {}
    net = None
    if os.path.exists(DEEP): net = DQN(); net.load(DEEP)
    for m in MODES:
        if m == "deep" and net is None: continue
        ts = []
        for sd in range(3):
            c = Controller(m, [r[:] for r in q] if m == "smart" else None, net=net if m == "deep" else None); c.learn = False
            ts.append(run(c, p["loss"], p["delay"], n_msgs=p["n"], rate=p["rate"], queue=p["queue"], seed=900 + sd, jitter=p["jitter"]))
        means[m] = st.mean(ts)
    return p, means

def evaluate(qpath, npaths=48, seeds=3, seed=424242):
    rng = random.Random(seed)
    paths = [sample_path(rng) for _ in range(npaths)]
    with Pool(os.cpu_count()) as pool:
        out = pool.map(one, [(qpath, p) for p in paths])
    res = {m: [o[1][m] for o in out] for m in out[0][1]}
    worst = [(o[1]["smart"] / o[1]["aimd"], o[0], o[1]) for o in out]
    return res, worst

if __name__ == "__main__":
    qpath = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "q_chat.txt")
    res, worst = evaluate(qpath)
    n = len(res["aimd"])
    sa = sum(1 for a, b in zip(res["smart"], res["aimd"]) if a < b * 0.95)
    sl = sum(1 for a, b in zip(res["smart"], res["aimd"]) if a > b * 1.05)
    sf = sum(1 for a, b in zip(res["smart"], res["fixed"]) if a < b * 0.95)
    sfl = sum(1 for a, b in zip(res["smart"], res["fixed"]) if a > b * 1.05)
    print(f"{n} random paths x 3 seeds")
    for m in res: print(f"  mean time {m:6s}: {st.mean(res[m]):7.2f} s   median {st.median(res[m]):6.2f} s")
    print(f"  Smart vs AIMD : faster on {sa}, slower on {sl}, within 5% on {n-sa-sl}")
    print(f"  Smart vs fixed: faster on {sf}, slower on {sfl}, within 5% on {n-sf-sfl}")
    if "deep" in res:
        for ref in ("aimd", "fixed", "smart"):
            w = sum(1 for a, b in zip(res["deep"], res[ref]) if a < b * 0.95); l = sum(1 for a, b in zip(res["deep"], res[ref]) if a > b * 1.05)
            print(f"  Deep  vs {ref:5s}: faster on {w}, slower on {l}, within 5% on {n-w-l}")
    worst.sort(key=lambda x: -x[0])
    print("  worst 6 paths for Smart vs AIMD:")
    for r, p, m in worst[:6]: print(f"    x{r:.2f} {p} -> " + ", ".join(f"{k} {v:.1f}" for k, v in m.items()))
