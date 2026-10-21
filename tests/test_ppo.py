#!/usr/bin/env python3
"""Behaviour tests for the REPLIC PPO controller, the counterpart of the Q-table checks in tests/test_sim.py.
   Needs results/smart/smart_final.npz (the trained weights) and src/neural_weights.h (the same weights compiled into C)."""
import os, subprocess, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import train_smart_replic as T
NPZ = os.path.join(ROOT, "results", "smart", "smart_final.npz")
fails = 0
def check(c, msg):
    global fails
    print(("ok:   " if c else "FAIL: ") + msg); fails += 0 if c else 1

z = np.load(NPZ)
check(z["W1"].shape == (32, 8) and z["W2"].shape == (32, 32) and z["W3"].shape == (1, 32), "weights load as an 8 -> 32 -> 32 -> 1 network")
check(all(np.isfinite(z[k]).all() for k in ("W1", "b1", "W2", "b2", "W3", "b3")), "no NaN or infinite weights")
pol = T.FixedPolicy(NPZ)
rng = np.random.default_rng(5)
X = np.concatenate([rng.uniform(-1, 1, (300, T.SM_IN)), rng.uniform(0, 1, (300, T.SM_IN)), rng.uniform(-50, 50, (50, T.SM_IN))])
A = np.array([pol.act(x, None)[0] for x in X])
check(np.all(np.abs(A) < 1.0 + 1e-12), "action stays in (-1, 1), so the window multiplier 2^a stays in (0.5, 2)")
check(np.all(A == np.array([pol.act(x, None)[0] for x in X])), "same input always gives the same action (deterministic)")
check(float(A.std()) > 0.05, "the action depends on the input (not a constant)")

# the policy in the simulator, alone on the 1000 pps / 44 ms / 60-packet link with 0.1% random loss
alone = [T.eval_pair("policy", "cubic", 0, s, pol, dur=15000.0) for s in (1, 2)]
aimd = [T.eval_pair("aimd", "cubic", 0, s, None, dur=15000.0) for s in (1, 2)]
check(all(r["util"] > 0.6 for r in alone), "alone, REPLIC fills more than 60%% of the link (%s)" % ", ".join("%.2f" % r["util"] for r in alone))
check(np.mean([r["util"] for r in alone]) >= np.mean([r["util"] for r in aimd]), "alone, REPLIC uses at least as much of the link as AIMD does")
check(all(r["qd"] < 1.0 for r in alone), "alone, REPLIC does not keep a standing queue (mean queue delay under 1 s)")
vs = [T.eval_pair("policy", "cubic", 1, s, pol, dur=15000.0) for s in (1, 2, 3)]
check(all(r["J"] > 0.9 for r in vs), "against one CUBIC flow the Jain fairness index is above 0.9 (%s)" % ", ".join("%.2f" % r["J"] for r in vs))
check(all(r["minrel"] > 0.6 for r in vs), "against one CUBIC flow nobody gets less than 60%% of a fair share (%s)" % ", ".join("%.2f" % r["minrel"] for r in vs))

# the C forward pass compiled from src/neural_weights.h must match this NumPy one
r = subprocess.run([sys.executable, os.path.join(HERE, "check_smart_export.py"), NPZ], capture_output=True, text=True)
check(r.returncode == 0, "C forward pass matches NumPy to 1e-5 (%s)" % (r.stdout.strip().splitlines() or ["no output"])[-1])
print("all PPO tests passed" if not fails else "%d FAILED" % fails)
sys.exit(1 if fails else 0)
