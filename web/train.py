#!/usr/bin/env python3
"""Train the chat's REPLIC Q-table on simulated paths, then evaluate on held-out paths and seeds.
   python3 web/train.py            -> writes results/q_chat.txt and prints the comparison table"""
import os, random, statistics as st, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc import Controller, prior_q, save_q
from sim import run

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "q_chat.txt")
TRAIN_LOSS = [0.0, 0.01, 0.02, 0.05, 0.08, 0.12, 0.15]
TRAIN_DELAY = [0.02, 0.04, 0.07, 0.10, 0.15]
TRAIN_RATE = [None, None, 100, 300]
# held-out evaluation paths (values not in the training grids, plus seeds never used in training)
TEST_PATHS = [("lossy 10%, 40 ms", 0.10, 0.04, None), ("clean-ish 2%, 100 ms", 0.02, 0.10, None),
              ("wireless 6%, 60 ms", 0.06, 0.06, None), ("lossy 4%, 50 ms, bottleneck", 0.04, 0.05, 250),
              ("clean, 30 ms, bottleneck", 0.0, 0.03, 120), ("harsh 14%, 90 ms", 0.14, 0.09, None)]

def train(episodes=6000, seed=7):
    rng = random.Random(seed)
    q = prior_q()
    c = Controller("smart", q, alpha=0.15, gamma=0.9, eps=0.3, rng=rng)
    for ep in range(episodes):
        c.eps = max(0.03, 0.3 * (1 - ep / (episodes * 0.8)))
        run(c, rng.choice(TRAIN_LOSS), rng.choice(TRAIN_DELAY), rate=rng.choice(TRAIN_RATE), seed=10_000 + ep)
    return q

def evaluate(q, seeds=60):
    rows = []
    for name, loss, delay, rate in TEST_PATHS:
        res = {}
        for mode in ("fixed", "aimd", "smart"):
            ts = []
            for s in range(seeds):
                c = Controller(mode, [r[:] for r in q] if mode == "smart" else None)
                c.learn = False; c.eps = 0.0                      # frozen, greedy
                ts.append(run(c, loss, delay, rate=rate, seed=s))
            res[mode] = (st.mean(ts), st.stdev(ts) / len(ts) ** 0.5)
        rows.append((name, res))
    return rows

if __name__ == "__main__":
    t0 = time.time()
    q = train()
    save_q(q, OUT)
    print(f"trained in {time.time()-t0:.0f}s -> {OUT}")
    print(f"{'path':32s} {'fixed':>12s} {'AIMD':>12s} {'Smart':>12s}   (mean s ± s.e., 60 held-out seeds, lower is better)")
    for name, r in evaluate(q):
        print(f"{name:32s} " + " ".join(f"{r[m][0]:6.2f}±{r[m][1]:.2f}  " for m in ("fixed", "aimd", "smart")))
