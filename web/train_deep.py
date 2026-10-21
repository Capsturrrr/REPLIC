#!/usr/bin/env python3
"""Train the neural (DQN) window controller in the simulator, then evaluate on the same held-out paths as train.py.
   python3 web/train_deep.py [episodes]   -> writes results/dqn_chat.json"""
import os, random, sys, time, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc import Controller, load_q
from dqn import DQN
from sim import run
from train import TRAIN_LOSS, TRAIN_DELAY, TRAIN_RATE, TEST_PATHS
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("REPLIC_OUT") or os.path.join(HERE, "..", "results", "dqn_chat.json")

def train(episodes=6000, seed=7, log=True):
    rng = random.Random(seed); net = DQN(seed=seed)
    c = Controller("deep", None, eps=0.3, rng=rng, net=net)
    t0 = time.time()
    for ep in range(episodes):
        c.eps = max(0.03, 0.3 * (1 - ep / (episodes * 0.8)))
        run(c, rng.choice(TRAIN_LOSS), rng.choice(TRAIN_DELAY), rate=rng.choice(TRAIN_RATE), seed=10_000 + ep)
        if log and ep % 500 == 0: print(f"ep {ep} buf {net.n} {time.time()-t0:.0f}s", flush=True)
    return net

def evaluate(net, seeds=40):
    rows = []
    q = load_q(os.path.join(HERE, "..", "results", "q_chat.txt"))
    for name, loss, delay, rate in TEST_PATHS:
        res = {}
        for mode in ("fixed", "aimd", "smart", "deep"):
            ts = []
            for s in range(seeds):
                c = Controller(mode, [r[:] for r in q] if mode == "smart" else None, net=net if mode == "deep" else None)
                c.learn = False; c.eps = 0.0
                ts.append(run(c, loss, delay, rate=rate, seed=s))
            res[mode] = st.mean(ts)
        rows.append((name, res))
    return rows

def validate(net, npaths=16, seeds=2):
    """Score a trained net on validation paths that are neither training, held-out test nor stress paths."""
    from stress import sample_path
    rng = random.Random(777); tot = 0.0
    for _ in range(npaths):
        p = sample_path(rng)
        for sd in range(seeds):
            c = Controller("deep", None, net=net); c.learn = False; c.eps = 0.0
            tot += run(c, p["loss"], p["delay"], n_msgs=p["n"], rate=p["rate"], queue=p["queue"], seed=5000 + sd, jitter=p["jitter"])
    return tot / (npaths * seeds)

def train_best(seeds=(7, 11, 12, 13), episodes=6000):
    best = None
    for sd in seeds:
        net = train(episodes, seed=sd, log=False); v = validate(net)
        print(f"seed {sd}: validation mean {v:.2f} s", flush=True)
        if best is None or v < best[0]: best = (v, sd, net)
    print(f"selected seed {best[1]} (validation {best[0]:.2f} s)")
    return best[2]

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
    net = train_best(episodes=n); net.save(OUT); print("saved", OUT)
    print(f"{'path':32s} fixed  AIMD  table  deep  (mean s, 40 held-out seeds)")
    for name, r in evaluate(net): print(f"{name:32s} " + " ".join(f"{r[m]:5.2f}" for m in ("fixed", "aimd", "smart", "deep")))
