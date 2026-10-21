#!/usr/bin/env python3
"""
gen_paper_tables.py -- turn raw.csv files into the LaTeX files that paper/section_V_scale_insert.tex \\input{}s.

    python3 gen_paper_tables.py                                   # proxy n=30 results only; the kernel-TCP files are written as visible "pending" stubs
    python3 gen_paper_tables.py --netem-scale results/wsl_netem/scale/raw.csv --netem-matrix results/wsl_netem/matrix/raw.csv

Output (default results/paper_tex/):  proxy_scale_table.tex  proxy_scale_paired.tex  proxy_scale_text.tex
                                      netem_scale_table.tex  netem_scale_paired.tex  netem_scale_text.tex   netem_matrix.tex
Every number in these files is computed from the raw CSVs. When a netem file is missing the corresponding output is a boxed
"PENDING" note, so the paper still compiles and nobody can mistake an empty table for a result.
"""
import argparse, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import run_all_tests as R
import run_scale_matrix as S

PENDING = "\\fbox{\\parbox{0.9\\columnwidth}{\\textbf{PENDING:} %s has not been run yet. Run \\texttt{execute\\_wsl\\_benchmarks.sh} on WSL2/Linux and regenerate with \\texttt{gen\\_paper\\_tables.py}.}}\n"


def scale_files(raw, tag, out_dir, note_prefix):
    rows = R.read_rows([raw]); n = max(int(r["rep"]) for r in rows) + 1
    tmp = tempfile.mkdtemp()
    backend = rows[0].get("backend", tag)
    note = ("UDP impairment proxy" if backend == "proxy" else "Linux netem+tbf on lo, kernel TCP included")
    S.emit(rows, tmp, note, n, tag=tag)
    for name in ("scale_table", "scale_paired", "scale_text"):
        shutil.copy(os.path.join(tmp, name + ".tex"), os.path.join(out_dir, f"{tag}_{name}.tex"))
    shutil.copy(os.path.join(tmp, "REPORT.txt"), os.path.join(out_dir, f"{tag}_REPORT.txt"))
    shutil.rmtree(tmp)
    return len(rows), n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proxy-scale", default=os.path.join(HERE, "results", "scale", "proxy", "raw.csv"))
    ap.add_argument("--netem-scale"); ap.add_argument("--netem-matrix")
    ap.add_argument("--fairness-raw", default=os.path.join(HERE, "results", "fairness_n30", "raw.csv"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "results", "paper_tex"))
    a = ap.parse_args(); os.makedirs(a.out_dir, exist_ok=True)
    if os.path.exists(a.proxy_scale):
        print("proxy scale: %d rows, n=%d seeds" % scale_files(a.proxy_scale, "proxy", a.out_dir, "proxy"))
    else:
        for nm in ("scale_table", "scale_paired", "scale_text"): open(os.path.join(a.out_dir, f"proxy_{nm}.tex"), "w").write(PENDING % "the proxy scale matrix")
    if a.netem_scale and os.path.exists(a.netem_scale):
        print("netem scale: %d rows, n=%d seeds" % scale_files(a.netem_scale, "netem", a.out_dir, "netem"))
    else:
        for nm in ("scale_table", "scale_paired", "scale_text"): open(os.path.join(a.out_dir, f"netem_{nm}.tex"), "w").write(PENDING % "the kernel-TCP netem scale matrix")
    if a.netem_matrix and os.path.exists(a.netem_matrix):
        rows = R.read_rows([a.netem_matrix]); summ = R.summarize(rows)
        R.write_tex(summ, os.path.join(a.out_dir, "netem_matrix.tex"), "Linux netem+tbf on lo, kernel TCP CUBIC/BBR vs REPLIC")
        print("netem matrix: %d rows" % len(rows))
    else:
        open(os.path.join(a.out_dir, "netem_matrix.tex"), "w").write(PENDING % "the netem profile matrix")
    fr = os.path.join(a.out_dir, "fairness30_rows.tex")
    if os.path.exists(a.fairness_raw):
        import csv, statistics as st
        cell = {}
        for r in csv.DictReader(open(a.fairness_raw)): cell.setdefault((r["subject"], r["background"], int(r["k"])), []).append(r)
        def ms(rs, k):
            v = [float(r[k]) for r in rs]; return st.mean(v), (st.stdev(v) if len(v) > 1 else float("nan"))
        lines = []
        if True:
            order = {"neural": 0, "neural_polite": 1, "smart_fair": 2}
            for (sub, bg, k), rs in sorted(cell.items(), key=lambda kv: (kv[0][1], kv[0][2], order.get(kv[0][0], 9))):
                J, S_, M = ms(rs, "J"), ms(rs, "subject_rel"), ms(rs, "min_rel")
                lines.append("%s & %s & %d & %d & %.3f $\\pm$ %.3f & %.2f $\\pm$ %.2f & %.2f $\\pm$ %.2f" % (sub.replace("_", "\\_"), bg.upper(), k, len(rs), J[0], J[1], S_[0], S_[1], M[0], M[1]))
        open(fr, "w").write(" \\\\\n".join(lines) + "%\n")      # no trailing row end: the paper puts \\ after \input
        print("fairness rows:", sum(len(v) for v in cell.values()))
    else:
        open(fr, "w").write("\\multicolumn{7}{c}{PENDING: fairness n=30 not run}%\n")
    print("written to", a.out_dir)


if __name__ == "__main__":
    main()
