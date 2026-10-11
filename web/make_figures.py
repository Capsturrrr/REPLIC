#!/usr/bin/env python3
"""Regenerates the headline figures from the virtual-time sender (about a minute): figures/headline.png, random_paths.png, cwnd_traces.png"""
import os, sys, random, math, subprocess, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vt, vt_random
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
out = os.path.join(vt.ROOT, "figures"); os.makedirs(out, exist_ok=True)
C = {"aimd_128": "#8b98a5", "cubic_128": "#e0a030", "deep": "#1f9d55"}; NAME = {"aimd_128": "AIMD (tuned)", "cubic_128": "CUBIC (tuned)", "deep": "Neural agent"}
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

# 1. headline: completion time on the five named paths, 100 paired seeds, 95% intervals
seeds = range(20001, 20101); fig, ax = plt.subplots(figsize=(8, 3.8)); w = 0.26
for k, a in enumerate(C):
    ms, hs = [], []
    for p in vt.PATHS:
        m, h = vt.mean_ci([r["time"] for r in vt.sweep(a, p, seeds)]); ms.append(m); hs.append(h)
    ax.bar([i + (k - 1) * w for i in range(5)], ms, w, yerr=hs, color=C[a], label=NAME[a], capsize=2)
ax.set_xticks(range(5)); ax.set_xticklabels(["5G-like", "Lossy 2%", "Satellite", "Fast, 3000 pkts", "Hostile 10%"]); ax.set_ylabel("completion time (s)")
ax.set_title("Neural agent vs tuned baselines, 100 paired seeds (virtual time)"); ax.legend(frameon=False); fig.tight_layout(); fig.savefig(f"{out}/headline.png", dpi=150); plt.close(fig)

# 2. 100 random held-out paths: distribution of time relative to tuned AIMD
rng = random.Random(424242); paths = [vt_random.draw(rng) for _ in range(100)]
def ratio(a):
    r = []
    for i, (pa, n) in enumerate(paths):
        x = vt.run(a, pa, 1 + i * 7, npk=n); y = vt.run("aimd_128", pa, 1 + i * 7, npk=n)
        r.append(x["time"] / y["time"])
    return r
fig, ax = plt.subplots(figsize=(8, 3.4)); bins = [0.2 * 1.12 ** k for k in range(0, 24)]
for a in ("deep", "cubic_128"):
    r = ratio(a); ax.hist(r, bins=bins, alpha=0.65, color=C[a], label=f"{NAME[a]}: geometric mean {math.exp(st.mean(math.log(x) for x in r)):.2f}")
ax.axvline(1, color="k", lw=1); ax.set_xscale("log"); ax.set_xlabel("completion time / tuned AIMD (left of the line = faster)"); ax.set_ylabel("paths")
ax.set_title("100 random held-out paths"); ax.legend(frameon=False); fig.tight_layout(); fig.savefig(f"{out}/random_paths.png", dpi=150); plt.close(fig)

# 3. congestion window over time on the lossy path
fig, ax = plt.subplots(figsize=(8, 3.4))
for a in ("aimd_128", "deep"):
    tf = f"/tmp/trace_{a}.csv"; arm = vt.ARMS[a]
    subprocess.run([vt.BIN, "--vt", "1", *arm.split(), *vt.PATHS["lossy"][0].split(), "--packets", "1200", "--seed", "3", "--trace", tf], capture_output=True)
    t, c = [], []
    for l in open(tf):
        f = l.strip().split(",")
        try: t.append(float(f[0]) / 1000); c.append(float(f[2]))
        except (ValueError, IndexError): pass
    ax.plot(t, c, color=C[a], lw=1.2, label=NAME[a])
ax.set_xlabel("time (s)"); ax.set_ylabel("congestion window (packets)"); ax.set_title("Window on the lossy path (2% loss, 25 ms)"); ax.legend(frameon=False); fig.tight_layout()
fig.savefig(f"{out}/cwnd_traces.png", dpi=150); plt.close(fig)
print("wrote", os.listdir(out))
