#!/usr/bin/env python3
"""Train the neural agent in 'simulator v2': in-order jitter, realistic fast-retransmit, adaptive RTO, and a mix of
transfer lengths (60 to 600 packets), so training looks like the C experiments. Run with REPLIC_RG=1 REPLIC_INORDER=1 REPLIC_ARTO=1.
   REPLIC_RG=1 REPLIC_INORDER=1 REPLIC_ARTO=1 python3 web/train_v2.py [episodes] -> results/dqn_v2.json"""
import os, random, sys, time, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
from cc import Controller
from dqn import DQN
from sim import run
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("REPLIC_OUT") or os.path.join(HERE, "..", "results", "dqn_v2.json")
LOSS = [0, 0.002, 0.005, 0.01, 0.02, 0.04, 0.07, 0.1, 0.15, 0.2, 0.3]
DELAY = [0.01, 0.025, 0.05, 0.08, 0.12, 0.15, 0.2, 0.3]
RATE = [None, 100, 250, 500, 800, 1500]
JIT = [0, 0.003, 0.01, 0.03]
NMSG = [60, 60, 200, 400, 800]

def train(episodes, seed):
    rng = random.Random(seed); net = DQN(seed=seed)
    c = Controller("deep", None, eps=0.3, rng=rng, net=net)
    for ep in range(episodes):
        c.eps = max(0.03, 0.3 * (1 - ep / (episodes * 0.8)))
        run(c, rng.choice(LOSS), rng.choice(DELAY), n_msgs=rng.choice(NMSG), rate=rng.choice(RATE), queue=rng.choice([20, 50, 100]),
            seed=10_000 + ep, jitter=rng.choice(JIT), max_t=60)
    return net

def validate(net):
    from stress import sample_path
    rng = random.Random(777); tot = 0.0
    for _ in range(16):
        p = sample_path(rng)
        for sd in range(2):
            c = Controller("deep", None, net=net); c.learn = False; c.eps = 0.0
            tot += run(c, p["loss"], p["delay"], n_msgs=max(p["n"], 200), rate=p["rate"], queue=p["queue"], seed=5000 + sd, jitter=p["jitter"], max_t=60)
    return tot / 32

def job(a):
    ep, sd = a
    net = train(ep, sd); return sd, validate(net), net

if __name__ == "__main__":
    ep = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    t0 = time.time()
    with Pool(2) as pool: res = pool.map(job, [(ep, s) for s in (7, 11, 12, 13)])
    for sd, v, _ in res: print(f"seed {sd}: validation {v:.2f} s")
    best = min(res, key=lambda r: r[1]); print(f"selected seed {best[0]} ({time.time()-t0:.0f}s)")
    best[2].save(OUT); print("saved", OUT)
