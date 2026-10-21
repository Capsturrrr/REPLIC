#!/usr/bin/env python3
"""
run_scale_matrix.py -- high-sample (default n = 30 seeds per cell) multi-path evaluation for REPLIC / REPLIC.

    python3 run_scale_matrix.py                         # proxy backend, 3 path families x 6 policies x 30 seeds (1 MB each), resumable
    python3 run_scale_matrix.py --n 3 --families dc_microburst --policies cubic,smart --out /tmp/x      # smoke test
    sudo python3 run_scale_matrix.py --backend netem    # WSL/Linux: kernel netem+tbf on lo, adds kernel TCP CUBIC and BBR as policies
    python3 run_scale_matrix.py --tables-only --raw results/scale/proxy/raw.csv results/scale/netem/raw.csv   # rebuild tables/text only

Design
  * Path families (dc_microburst, enterprise_campus, cellular_highlat) are *ranges*, not points. Seed s draws one concrete path from the family
    (delay, jitter, loss, rate, queue, cross traffic) with random.Random(f"{family}:{s}"), so path variance is part of every cell, and every
    policy sees exactly the same path for the same seed (paired design).
  * Policies are run interleaved per seed in a seed-dependent order, so slow drift of the machine load does not favour one policy.
  * Per cell: n, n_ok, mean with t-based 95% CI, median and p95 with bootstrap 95% CI (percentile, 4000 resamples), p99 (point estimate: with
    n = 30 it interpolates between the two largest observations, so it is a point estimate without a meaningful CI, and is labelled that way).
  * Paired comparison against a reference policy (cubic, and tcp_cubic when kernel TCP was run): geometric-mean ratio of completion time with
    a t-based 95% CI on the log ratios, paired t-test, Wilcoxon signed-rank test, Holm correction within each family.
  * Failed or timed-out runs are kept in raw.csv with ok=0 and counted (n_ok/n) -- never silently dropped; statistics use runs with ok=1,
    and the pair is dropped only when either member failed (reported as n_pairs).
  * Resumable: rows already in raw.csv (family, policy, seed) are skipped, so a killed run continues where it stopped.
Honest limits are written into REPORT.txt next to the tables.
"""
import argparse, csv, math, os, random, statistics as st, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_all_tests as R

U = random.Random.uniform
# one-way delay (ms), jitter (ms), loss (%), rate (kB/s), queue (kB), optional cross-traffic burst process (on_ms, off_ms, pps as a multiple of the bottleneck rate)
FAMILIES = {
    "dc_microburst": dict(
        note="datacenter-like: sub-ms to 1 ms one-way delay, 8-12 MB/s, shallow 20-40 kB buffer, no random loss, short 1-3 ms line-rate cross-traffic bursts every 20-40 ms",
        delay=(0.25, 1.0), jitter=(0, 0), loss=(0, 0), rate=(8000, 12000), queue=(20, 40), cross=((1, 3), (20, 40), (1.5, 1.5))),
    "enterprise_campus": dict(
        note="enterprise campus: 2-8 ms one-way delay, 0-2 ms jitter, up to 0.1% loss, 2.5-5 MB/s, 60-120 kB buffer, moderate on/off cross traffic",
        delay=(2, 8), jitter=(0, 2), loss=(0, 0.1), rate=(2500, 5000), queue=(60, 120), cross=((20, 60), (100, 300), (0.5, 0.5))),
    "cellular_highlat": dict(
        note="high-latency cellular: 40-120 ms one-way delay, 10-40 ms jitter, 0.5-3% loss, 150-600 kB/s, 20-50 kB buffer",
        delay=(40, 120), jitter=(10, 40), loss=(0.5, 3.0), rate=(150, 600), queue=(20, 50), cross=None),
}
DEFAULT_POLICIES = ["aimd", "cubic", "bbr", "neural", "smart", "smart+fec"]
KERNEL_POLICIES = ["tcp_cubic", "tcp_bbr"]
REFS = ["cubic", "tcp_cubic"]


def draw_path(family, seed, with_cross=True):
    f = FAMILIES[family]; rng = random.Random(f"{family}:{seed}")
    g = lambda k: round(U(rng, *f[k]), 3)
    sc = dict(name=family, seed=seed, delay=g("delay"), jitter=g("jitter"), loss=g("loss"), rate=g("rate"), queue=g("queue"), note=f["note"], cross="")
    if f["cross"] and with_cross:
        on = round(U(rng, *f["cross"][0]), 2); off = round(U(rng, *f["cross"][1]), 2); mult = U(rng, *f["cross"][2])
        pps = sc["rate"] * 1000.0 / R.WIRE * mult
        sc["cross"] = f"{on}:{off}:{pps:.0f}"
    return sc


# ----------------------------------------------------------------------------------------------- statistics
def tcrit(df):
    try:
        from scipy import stats
        return float(stats.t.ppf(0.975, df))
    except Exception:
        return 1.96 if df > 30 else {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 10: 2.23, 20: 2.09, 29: 2.045}.get(df, 2.0)


def mean_ci(x):
    n = len(x)
    if n == 0: return (float("nan"),) * 3
    m = st.mean(x)
    if n < 2: return m, float("nan"), float("nan")
    h = tcrit(n - 1) * st.stdev(x) / math.sqrt(n)
    return m, m - h, m + h


def boot_ci(x, fn, reps=4000, seed=12345):
    if len(x) < 3: return float("nan"), float("nan")
    rng = random.Random(seed); n = len(x); vals = []
    for _ in range(reps):
        vals.append(fn([x[rng.randrange(n)] for _ in range(n)]))
    vals.sort()
    return vals[int(0.025 * reps)], vals[int(0.975 * reps) - 1]


def holm(ps):
    idx = sorted(range(len(ps)), key=lambda i: ps[i]); adj = [None] * len(ps); run = 0.0
    for rank, i in enumerate(idx):
        v = min(1.0, (len(ps) - rank) * ps[i]); run = max(run, v); adj[i] = run
    return adj


def summarize_cells(rows):
    cells = {}
    for r in rows: cells.setdefault((r["scenario"], int(r["size"]), r["policy"]), []).append(r)
    out = []
    for (fam, size, pol), rs in sorted(cells.items()):
        good = [float(r["ms"]) for r in rs if int(r["ok"]) and r["ms"] != ""]
        m, lo, hi = mean_ci(good)
        med = st.median(good) if good else float("nan"); p95 = R.pct(good, 95); p99 = R.pct(good, 99)
        mlo, mhi = boot_ci(good, st.median); plo, phi = boot_ci(good, lambda v: R.pct(v, 95))
        gp = [float(r["goodput_MBps"]) for r in rs if int(r["ok"]) and r["goodput_MBps"] != ""]
        out.append(dict(family=fam, size=size, policy=pol, n=len(rs), n_ok=len(good), mean_ms=m, mean_lo=lo, mean_hi=hi, sd_ms=st.stdev(good) if len(good) > 1 else float("nan"),
                        median_ms=med, median_lo=mlo, median_hi=mhi, p95_ms=p95, p95_lo=plo, p95_hi=phi, p99_ms=p99,
                        goodput_median_MBps=st.median(gp) if gp else float("nan")))
    return out


def paired(rows):
    """policy vs reference, matched on (family, size, seed)."""
    try:
        from scipy import stats
    except Exception:
        stats = None
    by = {}
    for r in rows:
        if int(r["ok"]) and r["ms"] != "": by[(r["scenario"], int(r["size"]), r["policy"], int(r["rep"]))] = float(r["ms"])
    fams = sorted({(k[0], k[1]) for k in by}); pols = sorted({k[2] for k in by}); out = []
    for fam, size in fams:
        block = []
        for ref in REFS:
            if ref not in pols: continue
            for p in pols:
                if p == ref: continue
                seeds = sorted({k[3] for k in by if k[0] == fam and k[1] == size and k[2] == p} & {k[3] for k in by if k[0] == fam and k[1] == size and k[2] == ref})
                if len(seeds) < 3: continue
                lr = [math.log(by[(fam, size, p, s)] / by[(fam, size, ref, s)]) for s in seeds]
                m, lo, hi = mean_ci(lr)
                pt = pw = float("nan")
                if stats:
                    pt = float(stats.ttest_1samp(lr, 0.0).pvalue)
                    try: pw = float(stats.wilcoxon(lr).pvalue)
                    except ValueError: pw = float("nan")
                wins = sum(1 for v in lr if v < 0)
                block.append(dict(family=fam, size=size, policy=p, ref=ref, n_pairs=len(seeds), ratio=math.exp(m), ratio_lo=math.exp(lo), ratio_hi=math.exp(hi),
                                  p_t=pt, p_wilcoxon=pw, faster_in=wins))
        ps = [b["p_t"] for b in block if not math.isnan(b["p_t"])]
        if ps:
            adj = iter(holm(ps))
            for b in block: b["p_holm"] = next(adj) if not math.isnan(b["p_t"]) else float("nan")
        out += block
    return out


# ----------------------------------------------------------------------------------------------- LaTeX
def f0(v): return "--" if v != v else (f"{v:.0f}" if v >= 100 else f"{v:.1f}")
def esc(s): return s.replace("_", r"\_").replace("+", "+")


def write_tables(cells, pairs, out_dir, backend_note, n_seeds, tag=''):
    fams = [f for f in FAMILIES if any(c["family"] == f for c in cells)]
    pols = [p for p in (KERNEL_POLICIES + DEFAULT_POLICIES) if any(c["policy"] == p for c in cells)]
    size = cells[0]["size"] if cells else 0
    with open(os.path.join(out_dir, "scale_table.tex"), "w") as f:
        f.write(f"% auto-generated by run_scale_matrix.py -- {backend_note}\n% completion time in ms; 95% CI: t-interval (mean), bootstrap percentile interval (median, p95); p99 is a point estimate\n")
        f.write("\\begin{table*}[t]\\centering\\footnotesize\n")
        f.write(f"\\caption{{Completion time of a {size // 1000} kB transfer over randomly drawn paths, $n={n_seeds}$ seeds per cell ({esc(backend_note)}). "
                "Mean with 95\\% $t$-interval; median and p95 with 95\\% bootstrap interval; p99 is a point estimate. $n_{ok}$ = runs delivered byte-identically.}\n")
        f.write("\\label{tab:scale" + ("-" + tag if tag else "") + "}\\begin{tabular}{llrrrrr}\\toprule\nPath family & Policy & $n_{ok}$ & mean [95\\% CI] & median [95\\% CI] & p95 [95\\% CI] & p99\\\\\\midrule\n")
        for fam in fams:
            first = True
            for p in pols:
                c = [x for x in cells if x["family"] == fam and x["policy"] == p]
                if not c: continue
                c = c[0]
                f.write(f"{esc(fam) if first else ''} & {esc(p)} & {c['n_ok']}/{c['n']} & {f0(c['mean_ms'])} [{f0(c['mean_lo'])}, {f0(c['mean_hi'])}] & "
                        f"{f0(c['median_ms'])} [{f0(c['median_lo'])}, {f0(c['median_hi'])}] & {f0(c['p95_ms'])} [{f0(c['p95_lo'])}, {f0(c['p95_hi'])}] & {f0(c['p99_ms'])} \\\\\n")
                first = False
            f.write("\\midrule\n")
        f.write("\\bottomrule\\end{tabular}\\end{table*}\n")
    with open(os.path.join(out_dir, "scale_paired.tex"), "w") as f:
        f.write("% paired comparison, matched on the drawn path (seed); ratio < 1 means the policy finished faster than the reference\n")
        f.write("\\begin{table*}[t]\\centering\\footnotesize\n\\caption{Paired completion-time ratio versus the reference (same seed $\\Rightarrow$ same path). Geometric mean with 95\\% $t$-interval on the log ratio; "
                "Holm-adjusted paired $t$ $p$-value within each family and reference. Ratio $<1$: faster than the reference.}\n\\label{tab:scalepaired" + ("-" + tag if tag else "") + "}\\begin{tabular}{lllrrr}\\toprule\n")
        f.write("Family & Policy & vs & ratio [95\\% CI] & faster in & $p_{Holm}$\\\\\\midrule\n")
        for b in pairs:
            ph = b.get("p_holm", float("nan"))
            f.write(f"{esc(b['family'])} & {esc(b['policy'])} & {esc(b['ref'])} & {b['ratio']:.2f} [{b['ratio_lo']:.2f}, {b['ratio_hi']:.2f}] & {b['faster_in']}/{b['n_pairs']} & "
                    f"{'--' if ph != ph else ('$<$0.001' if ph < 0.001 else f'{ph:.3f}')} \\\\\n")
        f.write("\\bottomrule\\end{tabular}\\end{table*}\n")
    # data-driven sentences: every number below is computed from raw.csv, none is typed by hand
    with open(os.path.join(out_dir, "scale_text.tex"), "w") as f:
        f.write("% auto-generated sentences; regenerate, do not edit by hand\n")
        for ref in REFS:
            sel = [b for b in pairs if b["ref"] == ref and b["policy"] in ("smart", "smart+fec", "neural")]
            if not sel: continue
            label = {"cubic": "CUBIC running over REPLIC", "tcp_cubic": "kernel TCP CUBIC"}[ref]
            f.write(f"\\paragraph{{Versus {label}.}} ")
            parts = []
            for b in sel:
                if b["policy"] != "smart": continue
                ph = b.get("p_holm", float("nan"))
                verdict = ("faster" if b["ratio_hi"] < 1 else "slower" if b["ratio_lo"] > 1 else "not distinguishable from the reference")
                parts.append(f"on \\texttt{{{esc(b['family'])}}} REPLIC's completion time is {b['ratio']:.2f}$\\times$ that of the reference (95\\% CI {b['ratio_lo']:.2f}--{b['ratio_hi']:.2f}, "
                             f"{b['faster_in']} of {b['n_pairs']} paired seeds faster; Holm $p$ {'n/a' if ph != ph else ('$<0.001$' if ph < 0.001 else f'= {ph:.3f}')}): {verdict}")
            f.write("; ".join(parts) + ".\n\n")


def write_report(cells, pairs, out_dir, backend_note, n_seeds, args):
    with open(os.path.join(out_dir, "REPORT.txt"), "w") as f:
        f.write(f"backend: {backend_note}\nseeds per cell requested: {n_seeds}\nfamilies:\n")
        for k, v in FAMILIES.items(): f.write(f"  {k}: {v['note']}\n")
        f.write("\nLIMITS\n  * path families are our own parameter ranges, not measurements of real networks\n"
                "  * dc_microburst cross traffic is a synthetic on/off process in the proxy; it does not exist under netem (the netem backend keeps delay/jitter/loss/rate/buffer only)\n"
                "  * with n=30, p99 interpolates between the two largest samples; report it as a point estimate\n"
                "  * the proxy impairs the forward path (delay both ways, loss/queue forward only); netem on lo impairs both directions -- do not mix the two in one comparison\n"
                "  * a single 1 MB transfer per run: short-flow behaviour (slow start, handshake) dominates on dc_microburst\n"
                "  * confidence intervals describe the sampling error over drawn paths and run noise together, not any one real path\n\n")
        for c in cells:
            f.write(f"{c['family']:18s} {c['policy']:10s} n_ok {c['n_ok']:2d}/{c['n']:2d}  mean {c['mean_ms']:9.1f} [{c['mean_lo']:.1f},{c['mean_hi']:.1f}]  median {c['median_ms']:9.1f}  p95 {c['p95_ms']:9.1f}  p99 {c['p99_ms']:9.1f}\n")
        f.write("\n")
        for b in pairs:
            f.write(f"{b['family']:18s} {b['policy']:10s} vs {b['ref']:9s} ratio {b['ratio']:.3f} [{b['ratio_lo']:.3f},{b['ratio_hi']:.3f}] faster {b['faster_in']}/{b['n_pairs']} p_t {b['p_t']:.4f} p_wilcoxon {b['p_wilcoxon']:.4f} p_holm {b.get('p_holm', float('nan')):.4f}\n")


def write_csv(path, rows):
    if not rows: return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)


def emit(rows, out_dir, backend_note, n_seeds, args=None, tag=''):
    cells = summarize_cells(rows); pairs = paired(rows)
    write_csv(os.path.join(out_dir, "scale_summary.csv"), cells); write_csv(os.path.join(out_dir, "scale_paired.csv"), pairs)
    write_tables(cells, pairs, out_dir, backend_note, n_seeds, tag); write_report(cells, pairs, out_dir, backend_note, n_seeds, args)
    return cells, pairs


# ----------------------------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=30, help="seeds (independent runs) per cell")
    ap.add_argument("--backend", choices=["proxy", "netem"], default="proxy")
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--policies", help="comma list; default: " + ",".join(DEFAULT_POLICIES) + " (+ tcp_cubic,tcp_bbr with --backend netem)")
    ap.add_argument("--size", type=int, default=1000000)
    ap.add_argument("--out", help="output directory (default results/scale/<backend>)")
    ap.add_argument("--tables-only", action="store_true"); ap.add_argument("--raw", nargs="+", help="with --tables-only: raw.csv files to merge")
    a = ap.parse_args()
    out_dir = a.out or os.path.join(HERE, "results", "scale", a.backend); os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "raw.csv")
    if a.tables_only:
        rows = R.read_rows(a.raw or [raw_path]); emit(rows, out_dir, "merged: " + ", ".join(os.path.relpath(p, HERE) for p in (a.raw or [raw_path])), a.n)
        print(f"tables rebuilt from {len(rows)} rows -> {out_dir}"); return
    fams = a.families.split(",")
    for fam in fams:
        if fam not in FAMILIES: sys.exit(f"unknown family {fam}; choose from {list(FAMILIES)}")
    pols = a.policies.split(",") if a.policies else (KERNEL_POLICIES if a.backend == "netem" else []) + DEFAULT_POLICIES
    backend, note, dist, mtu = a.backend, "UDP impairment proxy (forward-path loss, uniform jitter, synthetic cross traffic)", True, None
    if a.backend == "netem":
        ok, info = R.probe_netem()
        if not ok: sys.exit(f"netem backend unavailable: {info}. Run through execute_wsl_benchmarks.sh, or use --backend proxy.")
        dist, mtu = info["dist"], info["mtu"]
        note = "Linux tc netem + tbf on lo (both directions), kernel TCP included" + ("" if dist else "; uniform jitter")
        import atexit; atexit.register(R.netem_clear, mtu)
        miss = [p for p in pols if R.is_tcp(p) and R.TCP_VARIANTS[p] and R.TCP_VARIANTS[p] not in R.tcp_cc_available()]
        if miss: print(f"WARNING: kernel lacks {miss}; skipped (sudo modprobe tcp_bbr)", flush=True); pols = [p for p in pols if p not in miss]
    elif any(R.is_tcp(p) for p in pols):
        print("note: kernel TCP needs --backend netem; skipped", flush=True); pols = [p for p in pols if not R.is_tcp(p)]
    R.OUT = os.path.join(out_dir, "work_dir"); os.makedirs(R.OUT, exist_ok=True)
    R.build()
    done = set(); rows = []
    if os.path.exists(raw_path):
        rows = R.read_rows([raw_path]); done = {(r["scenario"], r["policy"], int(r["rep"])) for r in rows}
        print(f"resuming: {len(rows)} rows already in {raw_path}", flush=True)
    paths_f = os.path.join(out_dir, "paths.csv"); newp = not os.path.exists(paths_f)
    pf = open(paths_f, "a", newline=""); pw = csv.writer(pf)
    if newp: pw.writerow(["family", "seed", "delay_ms", "jitter_ms", "loss_pct", "rate_kBps", "queue_kB", "cross_on_off_pps"])
    rf = open(raw_path, "a", newline=""); rw = None
    total = a.n * len(fams) * len(pols); k = len(done); t0 = time.time()
    for seed in range(a.n):
        for fam in fams:
            sc = draw_path(fam, seed, with_cross=(a.backend == "proxy"))
            if not any((fam, p, seed) not in done for p in pols): continue
            pw.writerow([fam, seed, sc["delay"], sc["jitter"], sc["loss"], sc["rate"], sc["queue"], sc["cross"]]); pf.flush()
            order = pols[:]; random.Random(f"order:{fam}:{seed}").shuffle(order)
            if a.backend == "netem": R.netem_apply(sc, dist)
            for pol in order:
                if (fam, pol, seed) in done: continue
                tag = f"{fam}_{pol.replace('+', 'p')}_{seed}"
                r = R.run_cell(sc, pol, a.size, seed, a.backend, tag); r["backend"] = a.backend; rows.append(r); k += 1
                if rw is None:
                    rw = csv.DictWriter(rf, fieldnames=list(r.keys()))
                    if rf.tell() == 0: rw.writeheader()
                rw.writerow(r); rf.flush()
                eta = (time.time() - t0) / max(1, k - len(done)) * (total - k) if k > len(done) else 0
                print(f"[{k}/{total}] {fam:18s} {pol:10s} seed {seed:2d} ok={r['ok']} ms={r['ms']}  eta {eta / 60:.0f} min", flush=True)
            # remove the per-run trace files: 540+ traces are not needed for the statistics
            tdir = os.path.join(R.OUT, "traces")
            if os.path.isdir(tdir):
                for fn in os.listdir(tdir):
                    try: os.remove(os.path.join(tdir, fn))
                    except OSError: pass
    rf.close(); pf.close()
    cells, pairs = emit(rows, out_dir, note, a.n)
    bad = [r for r in rows if not int(r["ok"])]
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min: {len(rows) - len(bad)}/{len(rows)} byte-identical; outputs in {out_dir}")
    if bad: print("NOT OK:", sorted({(r['scenario'], r['policy']) for r in bad}))


if __name__ == "__main__":
    main()
