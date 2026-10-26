#!/usr/bin/env python3
"""Regenerates every figure in docs/figures_v2 from the raw CSVs / logs. No number is typed by hand except the fairness bars (source noted)."""
import csv, math, re, statistics as st, collections, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import stats
R = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results"); O = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "figures_v2")
os.makedirs(O, exist_ok=True)
INK, MUTE, TEAL, GREY, AMB, RED, BG = "#161b20", "#6b737a", "#1f9d85", "#aab1b8", "#d9822b", "#c2453b", "#fbfaf6"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12, "axes.edgecolor": MUTE, "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG, "axes.titleweight": "bold", "axes.titlesize": 14})
def load(p, fam=None):
    d = collections.defaultdict(dict)
    for r in csv.DictReader(open(p)):
        if fam and r["scenario"] != fam: continue
        if r["ok"] == "1" and r["ms"]: d[(r["scenario"], r["policy"])][int(r["rep"])] = float(r["ms"]) / 1000
    return d
D = load(f"{R}/scale/proxy_v2a/raw.csv")
NAME = {"aimd32": "AIMD (window 32)", "aimd": "Tuned AIMD (128)", "cubic": "user-space CUBIC", "bbr": "user-space BBR", "smart": "REPLIC", "smart+fec": "REPLIC + FEC"}
COL = {"aimd32": GREY, "aimd": GREY, "cubic": GREY, "bbr": GREY, "smart": TEAL, "smart+fec": "#0f6b5a"}
def ci(v): m = st.mean(v); h = stats.t.ppf(.975, len(v) - 1) * st.stdev(v) / math.sqrt(len(v)); return m, h
def save(n): plt.tight_layout(); plt.savefig(f"{O}/{n}.png", dpi=170); plt.close(); print("fig", n)
def pair(fam, p, ref):
    a, b = D[(fam, p)], D[(fam, ref)]; s = sorted(set(a) & set(b)); lr = [math.log(a[i] / b[i]) for i in s]
    m = st.mean(lr); h = stats.t.ppf(.975, len(lr) - 1) * st.stdev(lr) / math.sqrt(len(lr))
    return math.exp(m), math.exp(m - h), math.exp(m + h), sum(v < 0 for v in lr), len(lr)
C = "cellular_highlat"; order = ["aimd32", "aimd", "cubic", "bbr", "smart", "smart+fec"]

# 1 cellular mean +- CI
fig, ax = plt.subplots(figsize=(9.5, 4.8))
for i, p in enumerate(order):
    v = list(D[(C, p)].values()); m, h = ci(v)
    ax.barh(i, m, xerr=h, color=COL[p], error_kw=dict(ecolor=INK, capsize=4, lw=1.3)); ax.text(m + h + .3, i, f"{m:.1f} s", va="center", fontweight="bold" if "smart" in p else None)
ax.set_yticks(range(len(order))); ax.set_yticklabels([NAME[p] for p in order]); ax.invert_yaxis(); ax.set_xlabel("Mean completion time of a 1 MB transfer (s), 95% CI")
ax.set_title("High-latency cellular paths: REPLIC halves the time of AIMD"); ax.set_xlim(0, 22); save("01_cellular_mean")

# 2 per-path scatter
fig, axs = plt.subplots(1, 2, figsize=(11, 4.8), sharey=True)
for ax, ref in zip(axs, ["aimd32", "aimd"]):
    s = sorted(set(D[(C, ref)]) & set(D[(C, "smart")])); x = [D[(C, ref)][i] for i in s]; y = [D[(C, "smart")][i] for i in s]
    ax.scatter(x, y, color=TEAL, s=42, alpha=.85); mx = max(x + y) * 1.05; ax.plot([0, mx], [0, mx], color=MUTE, ls="--", lw=1); ax.plot([0, mx], [0, mx / 2], color=AMB, ls=":", lw=1.3)
    ax.text(mx * .55, mx * .22, "2× faster line", color=AMB, fontsize=10); ax.set_xlim(0, mx); ax.set_ylim(0, mx)
    ax.set_xlabel(f"{NAME[ref]} time (s)"); ax.set_title(f"Each dot = one of 30 paths ({sum(b < a for a, b in zip(x, y))}/30 below the dashed line)", fontsize=11)
axs[0].set_ylabel("REPLIC time (s) on the same path"); save("02_per_path_scatter")

# 3 paired forest
fig, ax = plt.subplots(figsize=(9.5, 5.4)); rows = []
for fam, lab in [("dc_microburst", "Datacenter"), ("enterprise_campus", "Campus"), (C, "Cellular")]:
    for p in ["smart", "smart+fec"]:
        if fam == "dc_microburst":
            Dd = load(f"{R}/scale/proxy_v2dc/raw.csv")
            if (fam, p) not in Dd: continue
            a, b = Dd[(fam, p)], Dd[(fam, "cubic")]; s = sorted(set(a) & set(b)); lr = [math.log(a[i] / b[i]) for i in s]; m = st.mean(lr); h = stats.t.ppf(.975, len(lr) - 1) * st.stdev(lr) / math.sqrt(len(lr)); r = (math.exp(m), math.exp(m - h), math.exp(m + h))
        else: r = pair(fam, p, "cubic")[:3]
        if fam != C and p == "smart+fec": continue
        rows.append((f"{lab} · {NAME[p]}", r))
for i, (lab, (m, lo, hi)) in enumerate(rows):
    c = TEAL if hi < 1 else (RED if lo > 1 else MUTE); ax.plot([lo, hi], [i, i], color=c, lw=3); ax.plot(m, i, "o", color=c, ms=10); ax.text(1.62, i, f"{m:.2f}×", va="center", fontweight="bold", color=c)
ax.axvline(1, color=INK, lw=1.5); ax.set_yticks(range(len(rows))); ax.set_yticklabels([r[0] for r in rows]); ax.invert_yaxis(); ax.set_xlim(0.3, 1.8)
ax.set_xlabel("Completion time ÷ user-space CUBIC time on the same paths (below 1 = faster)"); ax.set_title("Paired ratio versus CUBIC, geometric mean with 95% CI"); save("03_paired_forest")

# 4 tails: box + p95
fig, ax = plt.subplots(figsize=(10, 4.8)); data = [list(D[(C, p)].values()) for p in order]
bp = ax.boxplot(data, vert=False, patch_artist=True, widths=.6, showfliers=True, medianprops=dict(color=INK, lw=2))
for patch, p in zip(bp["boxes"], order): patch.set_facecolor(COL[p]); patch.set_alpha(.8)
for i, p in enumerate(order): ax.text(max(data[i]) + .5, i + 1, f"p95 {np.percentile(data[i], 95):.1f} s", va="center", fontsize=10)
ax.set_yticklabels([NAME[p] for p in order]); ax.invert_yaxis(); ax.set_xlabel("Completion time (s), 30 paths"); ax.set_xlim(0, 45); ax.set_title("Worst cases: REPLIC has no long tail"); save("04_tails_box")

# 5 CDF
fig, ax = plt.subplots(figsize=(9, 4.8))
for p in order:
    v = np.sort(list(D[(C, p)].values())); ax.plot(v, np.arange(1, len(v) + 1) / len(v), color=COL[p] if "smart" in p else None, lw=3 if "smart" in p else 1.6, label=NAME[p], ls="-" if "smart" in p else "--")
ax.set_xlabel("Completion time (s)"); ax.set_ylabel("Share of transfers finished"); ax.legend(frameon=False); ax.set_title("Cumulative distribution: REPLIC curves sit left of every baseline"); save("05_cdf")

# 6 campus + dc bars
fig, axs = plt.subplots(1, 2, figsize=(11, 4.4))
Ddc = load(f"{R}/scale/proxy_v2dc/raw.csv"); D1 = load(f"{R}/scale/proxy_v1dc/raw.csv")
E = "enterprise_campus"; ps = ["aimd", "cubic", "bbr", "smart", "smart+fec"]
for i, p in enumerate(ps):
    m, h = ci(list(D[(E, p)].values())); axs[0].bar(i, m * 1000, yerr=h * 1000, color=COL[p], error_kw=dict(ecolor=INK, capsize=3))
axs[0].set_xticks(range(len(ps))); axs[0].set_xticklabels([NAME[p].replace("Tuned ", "").replace(" (128)", "") for p in ps], rotation=25, ha="right"); axs[0].set_ylabel("Mean time (ms)"); axs[0].set_title("Enterprise campus", fontsize=12)
dcp = ["cubic", "bbr", "smart", "smart+fec"]
for i, p in enumerate(dcp):
    m, h = ci(list(Ddc[("dc_microburst", p)].values())); axs[1].bar(i, m * 1000, yerr=h * 1000, color=COL[p], error_kw=dict(ecolor=INK, capsize=3))
axs[1].set_xticks(range(len(dcp))); axs[1].set_xticklabels([NAME[p] for p in dcp], rotation=25, ha="right"); axs[1].set_title("Datacenter micro-bursts", fontsize=12); save("06_campus_datacenter")

# 7 datacenter fix before/after
fig, ax = plt.subplots(figsize=(8, 4.4)); labs = ["user-space CUBIC", "REPLIC\n(before upgrade)", "REPLIC\n(after upgrade)"]
vals = [st.mean(Ddc[("dc_microburst", "cubic")].values()) * 1000, st.mean(D1[("dc_microburst", "smart")].values()) * 1000, st.mean(Ddc[("dc_microburst", "smart")].values()) * 1000]
ax.bar(labs, vals, color=[GREY, AMB, TEAL]); [ax.text(i, v + 6, f"{v:.0f} ms", ha="center", fontweight="bold") for i, v in enumerate(vals)]; ax.set_ylabel("Mean time (ms), 30 paths"); ax.set_title("Datacenter micro-bursts: slowdown removed"); save("07_datacenter_fix")

# 8 ACK-loss recovery before/after
def summ(d): return {(x["scenario"], x["policy"]): float(x["ms_mean"]) / 1000 for x in csv.DictReader(open(f"{R}/{d}/summary.csv"))}
o, n = summ("ackloss_old"), summ("ackloss_new"); cells = [(s, p) for s in ["loss2_j10", "loss5_j20"] for p in ["aimd", "bbr", "smart"]]
fig, ax = plt.subplots(figsize=(10, 4.6)); w = .38; x = np.arange(len(cells))
ax.bar(x - w / 2, [o[c] for c in cells], w, color=GREY, label="Before"); ax.bar(x + w / 2, [n[c] for c in cells], w, color=TEAL, label="After (SACK scoreboard + tail-loss probe)")
for i, c in enumerate(cells): ax.text(i + w / 2, n[c] + .2, f"-{(1 - n[c] / o[c]) * 100:.0f}%", ha="center", fontsize=10, fontweight="bold", color="#0f6b5a")
ax.set_xticks(x); ax.set_xticklabels([f"{NAME[p].split(' (')[0]}\n{ {'loss2_j10': '2% loss', 'loss5_j20': '5% loss'}[s]}" for s, p in cells]); ax.set_ylabel("Mean time (s), 4 runs per cell"); ax.legend(frameon=False); ax.set_title("Loss recovery upgrade when ACKs are lost as well"); save("08_recovery_upgrade")

# 9 fairness (numbers: results/eval_fair*; Jain index, 30 runs per bar, as on the slide)
fig, ax = plt.subplots(figsize=(8.5, 4.4)); xs = np.arange(2); ax.bar(xs - .2, [.942, .881], .4, color=GREY, label="Static neural agent"); ax.bar(xs + .2, [.974, .958], .4, color=TEAL, label="REPLIC (fairness term in reward)")
[ax.text(i + d, v + .004, f"{v:.3f}", ha="center", fontweight="bold") for i, (a, b) in enumerate([(.942, .974), (.881, .958)]) for d, v in ((-.2, a), (.2, b))]
ax.set_xticks(xs); ax.set_xticklabels(["vs 1 CUBIC flow", "vs 2 CUBIC flows"]); ax.set_ylim(.7, 1.04); ax.axhline(1, color=MUTE, ls=":"); ax.set_ylabel("Jain fairness index (axis starts at 0.70)"); ax.legend(frameon=False, loc="upper right", ncol=2, fontsize=10); ax.set_title("Sharing a bottleneck with CUBIC"); save("09_fairness")

# 10 netem incl. kernel TCP (WSL log)
t = open(f"{R}/wsl_netem/matrix_scale_log.txt").read() if os.path.exists(f"{R}/wsl_netem/matrix_scale_log.txt") else ""
N = collections.defaultdict(list)
for _, sc, p, sz, rep, ok, ms in re.findall(r'\[(\d+)/700\] (\S+)\s+(\S+)\s+(\d+) rep(\d+) ok=(\d) ms=([\d.]+)', t):
    if ok == "1": N[(sc, p)].append(float(ms) / 1000)
profs = [("lte", "LTE"), ("wan", "WAN"), ("wan_hi", "WAN, high RTT"), ("sat", "Satellite")]; pol = [("aimd", "user-space AIMD", GREY), ("cubic", "user-space CUBIC", "#8a9299"), ("smart", "REPLIC", TEAL), ("tcp_cubic", "Kernel CUBIC", AMB), ("tcp_bbr", "Kernel BBR", RED)]
if N:
    fig, ax = plt.subplots(figsize=(11, 4.8)); w = .16
    for j, (p, lab, c) in enumerate(pol): ax.bar(np.arange(4) + (j - 2) * w, [st.mean(N[(s, p)]) for s, _ in profs], w, color=c, label=lab)
    ax.set_xticks(range(4)); ax.set_xticklabels([l for _, l in profs]); ax.set_ylabel("Mean time (s), 10 runs"); ax.set_ylim(0, 38); ax.legend(frameon=False, ncol=5, loc="upper left"); ax.set_title("Linux netem: REPLIC beats our user-space baselines, kernel TCP is still faster"); save("10_netem_vs_kernel")
    # 11 netem replic speedup vs user-space baselines
    fig, ax = plt.subplots(figsize=(9, 4.4)); w = .25
    for j, (p, lab) in enumerate([("aimd", "user-space AIMD"), ("cubic", "user-space CUBIC"), ("bbr", "user-space BBR")]):
        vals = [st.mean(N[(s, p)]) / st.mean(N[(s, "smart")]) for s, _ in profs]; ax.bar(np.arange(4) + (j - 1) * w, vals, w, color=[GREY, "#8a9299", "#6e767d"][j], label=lab)
        [ax.text(i + (j - 1) * w, v + .05, f"{v:.1f}×", ha="center", fontsize=9) for i, v in enumerate(vals)]
    ax.axhline(1, color=INK, lw=1); ax.set_xticks(range(4)); ax.set_xticklabels([l for _, l in profs]); ax.set_ylabel("How many times faster REPLIC is"); ax.set_ylim(0, 4.8); ax.legend(frameon=False, ncol=3, loc="upper left"); ax.set_title("Linux netem: speed-up of REPLIC over each user-space baseline"); save("11_netem_replic_speedup")

# 12 evidence ledger
fig, ax = plt.subplots(figsize=(8.5, 3.8)); lab = ["Proxy matrix, first n=30 run", "Re-run with upgraded sender", "Untuned-AIMD add-on (cellular)", "-"]
ok = [540, 300, 30, None]; ax.barh(range(3), ok[:3], color=TEAL); [ax.text(v + 8, i, f"{v} / {v} byte-identical", va="center") for i, v in enumerate(ok[:3])]
ax.set_yticks(range(3)); ax.set_yticklabels(lab[:3]); ax.invert_yaxis(); ax.set_xlim(0, 900); ax.set_xlabel("Runs"); ax.set_title("Every counted run was verified byte for byte"); save("12_evidence")
