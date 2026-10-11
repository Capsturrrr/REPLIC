#!/usr/bin/env python3
"""Evolution-strategy training of the neural congestion controller directly against the real C sender (virtual time).
Because the environment is the C code itself, there is no simulator-to-C gap. Objective per generation, on a fresh batch of random paths:
    fitness = mean( log(time / time of tuned AIMD) ) + LAMBDA * retransmissions per packet        (lower is better)
Antithetic sampling, centred-rank fitness shaping, Adam on the ES gradient. Validation on 40 fixed held-out paths picks the checkpoint.
Usage: es_train.py OUT_FILE [generations] [start_file]"""
import math, os, random, sys, time, json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vt, vt_random
from concurrent.futures import ThreadPoolExecutor

CROSS = os.environ.get("ES_CROSS") == "1"   # odd-numbered paths also carry bursty cross traffic (--vcross), same for the AIMD reference
MU = float(os.environ.get("ES_MU", "3.0"))      # weight of the fairness penalty max(0, share - 0.5) on paths that carry an AIMD competitor
LAMBDA = float(os.environ.get("ES_LAMBDA", "1.5")); SIGMA = float(os.environ.get("ES_SIGMA", "0.15")); LR = float(os.environ.get("ES_LR", "0.03"))
POP = int(os.environ.get("ES_POP", "12")); BATCH = int(os.environ.get("ES_BATCH", "14"))
out = sys.argv[1]; gens = int(sys.argv[2]) if len(sys.argv) > 2 else 100; start = sys.argv[3] if len(sys.argv) > 3 else vt.DFILE
theta = np.array(open(start).read().split(), dtype=float); D = theta.size
MASK = np.zeros(D); MASK[-165:] = 1.0 if os.environ.get('ES_LAST') else 0.0
if not os.environ.get('ES_LAST'): MASK[:] = 1.0
tmp = "/tmp/es_w"; os.makedirs(tmp, exist_ok=True)
rng = random.Random(1); pool = [vt_random.draw(rng) for _ in range(300)]          # training pool (seed 1)
val = [vt_random.draw(random.Random(777 + i)) for i in range(40)]                 # held-out validation (seed 777+i); test set is seed 424242
ex = ThreadPoolExecutor(os.cpu_count() or 2)

def cross_args(pa, pi):
    """bursty cross traffic scaled to the path (deterministic per path index)"""
    if not CROSS or pi % 2 == 0: return ""
    r = random.Random(5000 + int(pi)); rate = float(pa.split("--rate")[1].split()[0])
    return f"--vcross {r.uniform(100, 600):.0f}:{r.uniform(200, 1000):.0f}:{rate * r.uniform(0.3, 0.9):.0f}"

def save(th, path): open(path, "w").write(" ".join(f"{x:.9g}" for x in th))
_ref = {}
def ref_time(pi, kind, paths):
    k = (kind, pi)
    if k not in _ref:
        pa, n = paths[pi]; _ref[k] = vt.run("aimd_128", pa, 100 + pi, npk=n, extra=("--vcomp 1" if (pi % 2 and not CROSS) else "") + " " + cross_args(pa, pi))["time"]
    return _ref[k]

def score(th, idxs, paths, kind, tag, serial=False):
    f = f"{tmp}/{tag}.txt"; save(th, f)
    def one(pi):
        pa, n = paths[pi]; comp = pi % 2 == 1 and not CROSS          # odd-numbered paths carry an AIMD competitor, even ones are single-flow
        r = vt.run("deep", pa, 100 + pi, npk=n, dfile=f, extra=("--vcomp 1" if comp else "") + " " + cross_args(pa, pi))
        if not r: return 3.0
        pen = MU * max(0.0, r["share"] - 0.5) if comp and r.get("share") is not None else 0.0
        return math.log(r["time"] / ref_time(pi, kind, paths)) + LAMBDA * r["retx"] / n + pen
    s = [one(pi) for pi in idxs] if serial else list(ex.map(one, idxs)); os.remove(f)
    return sum(s) / len(s)

# prime reference times
list(ex.map(lambda i: ref_time(i, "p", pool), range(len(pool)))); list(ex.map(lambda i: ref_time(i, "v", val), range(len(val))))
best_v = score(theta, range(len(val)), val, "v", "val0"); best = theta.copy()
print(f"start: validation fitness {best_v:+.4f} (0 = tuned AIMD, negative = faster)", flush=True)
m = np.zeros(D); v = np.zeros(D); b1, b2 = 0.9, 0.999; t0 = time.time(); r2 = np.random.RandomState(7)
for g in range(1, gens + 1):
    idxs = r2.choice(len(pool), BATCH, replace=False)
    eps = r2.randn(POP, D) * MASK; cands = [theta + SIGMA * e for e in eps] + [theta - SIGMA * e for e in eps]
    fits = np.array(list(ex.map(lambda ic: score(ic[1], idxs, pool, "p", f"g{ic[0]}", serial=True), enumerate(cands))))
    f_pos, f_neg = fits[:POP], fits[POP:]
    rk = np.empty(2 * POP); rk[np.argsort(-fits)] = np.arange(2 * POP); rk = rk / (2 * POP - 1) - 0.5      # high rank = low (good) fitness
    grad = ((rk[:POP] - rk[POP:])[:, None] * eps).sum(0) / (2 * POP * SIGMA)
    grad = grad * MASK
    m = b1 * m + (1 - b1) * grad; v = b2 * v + (1 - b2) * grad * grad
    theta = theta + LR * (m / (1 - b1 ** g)) / (np.sqrt(v / (1 - b2 ** g)) + 1e-8)
    cur = score(theta, idxs, pool, "p", "cur")
    msg = f"gen {g:3d}  batch fitness {cur:+.4f}  (pop mean {fits.mean():+.4f})  {time.time() - t0:5.0f}s"
    if g % 5 == 0 or g == gens:
        vv = score(theta, range(len(val)), val, "v", "valx"); msg += f"  validation {vv:+.4f}"
        if vv < best_v: best_v, best = vv, theta.copy(); save(best, out); msg += "  *"
    print(msg, flush=True)
save(best, out); print(f"best validation fitness {best_v:+.4f}; saved {out}", flush=True)
