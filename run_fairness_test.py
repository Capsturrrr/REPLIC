#!/usr/bin/env python3
"""
run_fairness_test.py - multi-flow coexistence test for Poise.

One subject flow (Poise neural agent, optionally with the courtesy guard --polite) shares ONE bottleneck with
k = 1..3 background flows (CUBIC or BBR). Output: Jain's fairness index, the subject's share relative to its fair
share 1/n, and a starvation metric (smallest flow's share relative to 1/n).

Backends
  proxy  (default, runs anywhere): bin/impair is the shared bottleneck (delay, jitter, loss, rate, drop-tail queue).
         Background flows are RUDP flows running the user-space CUBIC / simplified-BBR policies of this repo.
         Share = packets each flow got through the bottleneck when the first flow finished (printed by impair).
  netem  (needs root, tc, ip): the bottleneck is kernel netem on lo + a tbf rate limiter, and the background flows
         are KERNEL TCP with TCP_CONGESTION = cubic / bbr. Share = bytes each flow moved in a common time window.
         NOT executed in the sandbox where this was written (no tc/ip, no kernel BBR); run it on WSL2/Linux.

Jain's index:  J = (sum x)^2 / (n * sum x^2),   1/n <= J <= 1 (1 = perfectly even).

Usage
  python3 run_fairness_test.py --quick
  python3 run_fairness_test.py --reps 5 --background cubic,bbr --nbg 1,2,3 --subjects neural,neural_polite,aimd
  python3 run_fairness_test.py --backend netem --reps 5          (on a netem-capable machine)
"""
import argparse, csv, itertools, os, re, signal, socket, statistics as st, subprocess, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "results", "fairness")
BASE_PORT = 9930          # servers 9930+i ; proxy listens 9940+i
PROXY_PORT = 9940
WEIGHTS = os.path.join(ROOT, "results", "dqn_chat.txt")

# Bottleneck used in the earlier two-flow study (eval_fair.sh): 1000 pps, 60-packet queue, 30 ms one-way, light noise.
BOTTLENECK = dict(delay=30, jitter=3, loss=0.1, rate=1000, queue=60)

# Arm definitions: command-line flags for smart_client
ARMS = {
    "neural":        "--mode deep --init 10 --dfile %s" % WEIGHTS,
    "neural_polite": "--mode deep --init 10 --polite 1.3 --dfile %s" % WEIGHTS,
    "smart_fair":    "--mode smart --init 10",                      # Poise, weights compiled in (src/neural_weights.h)
    "smart_nofair":  "--mode smart --init 10 --dfile %s" % os.path.join(ROOT, "results", "smart", "smart_nofair.txt"),   # ablation: same training, fairness weight 0
    "aimd":          "--mode aimd --init 10 --maxcwnd 128 --ssthresh 128",
    "cubic":         "--mode cubic --init 10 --maxcwnd 128 --ssthresh 128",
    "bbr":           "--mode bbr --init 10 --maxcwnd 128 --ssthresh 128",
}


def jain(x):
    s = sum(x); q = sum(v * v for v in x)
    return (s * s) / (len(x) * q) if q > 0 else float("nan")


def shares_metrics(x):
    """x[0] is the subject. Returns J, subject share / fair share, min share / fair share."""
    n = len(x); tot = float(sum(x))
    if tot <= 0: return float("nan"), float("nan"), float("nan")
    fair = tot / n
    return jain(x), x[0] / fair, min(x) / fair


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, **kw)


def build():
    cf = "-O2 -std=c11 -D_POSIX_C_SOURCE=200809L -D_GNU_SOURCE -w"
    os.makedirs(os.path.join(ROOT, "bin"), exist_ok=True)
    for cmd in (f"gcc {cf} -o bin/server src/server.c src/common.c",
                f"gcc {cf} -o bin/smart_client src/smart_client.c src/rudp_sender.c src/common.c -lm",
                f"gcc {cf} -o bin/impair src/impair.c -lm"):
        r = sh(cmd)
        if r.returncode: sys.exit("build failed: %s\n%s" % (cmd, r.stderr))


def kill_all():
    for n in ("server", "impair", "smart_client"):
        subprocess.run(["pkill", "-x", n], capture_output=True)


# ---------------------------------------------------------------- proxy backend
def run_proxy(subject, bg_arm, k, packets, seed, bn=BOTTLENECK, timeout=240):
    arms = [subject] + [bg_arm] * k
    n = len(arms)
    procs = []
    env = dict(os.environ, RUDP_QUIET="1")
    try:
        for i in range(n):
            e = dict(env, RUDP_PORT=str(BASE_PORT + i), RUDP_OUT="/dev/null")
            procs.append(subprocess.Popen([os.path.join(ROOT, "bin/server")], cwd=ROOT, env=e,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        pairs = ["%d:%d" % (PROXY_PORT + i, BASE_PORT + i) for i in range(n)]
        pxf = tempfile.NamedTemporaryFile("w+", delete=False)
        px = subprocess.Popen([os.path.join(ROOT, "bin/impair")] + pairs +
                              ["--delay", str(bn["delay"]), "--jitter", str(bn["jitter"]), "--loss", str(bn["loss"]),
                               "--rate", str(bn["rate"]), "--queue", str(bn["queue"]), "--seed", str(seed)],
                              cwd=ROOT, stdout=pxf, stderr=subprocess.DEVNULL)
        procs.append(px)
        time.sleep(0.4)
        cl = []
        outs = []
        for i, a in enumerate(arms):
            f = tempfile.NamedTemporaryFile("w+", delete=False); outs.append(f)
            cmd = [os.path.join(ROOT, "bin/smart_client")] + ARMS[a].split() + \
                  ["--emu", "0", "--port", str(PROXY_PORT + i), "--packets", str(packets), "--seed", str(seed * 10 + i)]
            cl.append(subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT))
        t0 = time.time()
        for c in cl:
            try: c.wait(timeout=max(1, timeout - (time.time() - t0)))
            except subprocess.TimeoutExpired: c.kill()
        time.sleep(0.2)
        pxf.flush(); pxf.seek(0)
        m = re.search(r"SHARE((?:\s+\d+)+)", pxf.read())
        shares = [int(v) for v in m.group(1).split()] if m else None
        times = []
        for f in outs:
            f.flush(); f.seek(0); t = f.read()
            r = re.search(r"RESULT,([^\n]*)", t)
            times.append(float(r.group(1).split(",")[2]) if r else float("nan"))
        return shares, times
    finally:
        for p in procs:
            try: p.terminate()
            except Exception: pass
        for p in procs:
            try: p.wait(timeout=2)
            except Exception: p.kill()
        kill_all()


# ---------------------------------------------------------------- netem backend (kernel TCP background) - untested here
def netem_ready():
    if os.geteuid() != 0: return "not root"
    for t in ("tc", "ip"):
        if sh("command -v " + t).returncode: return t + " missing"
    return None


def run_netem(subject, bg_arm, k, packets, seed, bn=BOTTLENECK, window=15.0):
    """Subject = Poise over lo (netem delay/jitter/loss + tbf); background = k kernel TCP senders (cubic/bbr).
    Shares = bytes moved in the common `window` seconds (RUDP: bytes from the server's receive counter is not exposed,
    so we use packets*1200 reported by the sender; TCP: bytes accepted by send() minus nothing - approximation)."""
    why = netem_ready()
    if why: raise RuntimeError("netem backend unavailable: " + why)
    rate_bps = int(bn["rate"] * 1200 * 8)
    sh("tc qdisc del dev lo root 2>/dev/null")
    r = sh("tc qdisc add dev lo root handle 1: netem delay %dms %dms loss %.3f%% && "
           "tc qdisc add dev lo parent 1: handle 2: tbf rate %d burst 32kb latency 100ms" %
           (bn["delay"], bn["jitter"], bn["loss"], rate_bps))
    if r.returncode: raise RuntimeError("tc failed: " + r.stderr)
    try:
        stop = time.time() + window
        counts = [0] * k
        tcp_port = 9950
        def sink():
            s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", tcp_port)); s.listen(8)
            while time.time() < stop + 1:
                s.settimeout(1)
                try: c, _ = s.accept()
                except Exception: continue
                threading.Thread(target=lambda c=c: [None for _ in iter(lambda: c.recv(1 << 16), b"")], daemon=True).start()
        threading.Thread(target=sink, daemon=True).start()
        def bg(i):
            s = socket.socket()
            s.setsockopt(socket.IPPROTO_TCP, getattr(socket, "TCP_CONGESTION", 13), bg_arm.encode())
            s.connect(("127.0.0.1", tcp_port)); buf = b"x" * 65536
            while time.time() < stop:
                counts[i] += s.send(buf)
            s.close()
        th = [threading.Thread(target=bg, args=(i,)) for i in range(k)]
        for t in th: t.start()
        shares, times = run_proxy_netem_subject(subject, packets, seed, window)
        for t in th: t.join()
        return [shares] + counts, times
    finally:
        sh("tc qdisc del dev lo root 2>/dev/null")



def run_proxy_netem_subject(subject, packets, seed, window):
    """Run the Poise subject over lo (netem impairs it). Bytes moved = size of the server's output file at the end
    of the window (the server appends delivered payloads; it flushes in 4 kB blocks, so +-4 kB)."""
    outp = tempfile.mktemp()
    env = dict(os.environ, RUDP_QUIET="1", RUDP_OUT=outp, RUDP_PORT=str(BASE_PORT))
    srv = subprocess.Popen([os.path.join(ROOT, "bin/server")], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    c = None
    try:
        cmd = [os.path.join(ROOT, "bin/smart_client")] + ARMS[subject].split() + \
              ["--emu", "0", "--port", str(BASE_PORT), "--packets", "60000", "--seed", str(seed)]
        c = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(window)
        got = os.path.getsize(outp) if os.path.exists(outp) else float("nan")
        return got, []
    finally:
        if c: c.kill()
        srv.terminate()


# ---------------------------------------------------------------- driver
def welch(a, b):
    try:
        from scipy import stats
    except Exception:
        return None
    if len(a) < 2 or len(b) < 2: return None
    r = stats.ttest_ind(a, b, equal_var=False)
    return float(r.statistic), float(r.pvalue)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["proxy", "netem"], default="proxy")
    ap.add_argument("--subjects", default="neural,neural_polite,aimd")
    ap.add_argument("--background", default="cubic,bbr")
    ap.add_argument("--nbg", default="1,2,3", help="number of background flows (1-3, so 2-4 flows in total)")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--packets", type=int, default=3000)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    if a.quick: a.reps, a.packets, a.nbg, a.background, a.subjects = 1, 1500, "1,2", "cubic", "neural,neural_polite"
    os.makedirs(a.out, exist_ok=True)
    build()
    raw = os.path.join(a.out, "raw.csv")
    fresh = not os.path.exists(raw)
    f = open(raw, "a", newline=""); w = csv.writer(f)
    if fresh: w.writerow(["backend", "subject", "background", "k", "rep", "shares", "J", "subject_rel", "min_rel", "times_ms"])
    cells = list(itertools.product(a.subjects.split(","), a.background.split(","), [int(x) for x in a.nbg.split(",")]))
    for (sub, bg, k) in cells:
        for rep in range(1, a.reps + 1):
            kill_all()
            try:
                if a.backend == "proxy": shares, times = run_proxy(sub, bg, k, a.packets, rep)
                else: shares, times = run_netem(sub, bg, k, a.packets, rep)
            except RuntimeError as e:
                sys.exit(str(e))
            if not shares or len(shares) != k + 1:
                print("  no SHARE line", sub, bg, k, rep); continue
            J, srel, mrel = shares_metrics(shares)
            w.writerow([a.backend, sub, bg, k, rep, " ".join(map(str, shares)), "%.4f" % J, "%.3f" % srel, "%.3f" % mrel,
                        " ".join("%.0f" % t for t in times)]); f.flush()
            print("%-14s vs %dx%-5s rep %d shares=%s J=%.3f subj=%.2fx min=%.2fx" % (sub, k, bg, rep, shares, J, srel, mrel), flush=True)
    f.close()
    summarize(raw, a.out)


def summarize(raw, out):
    rows = list(csv.DictReader(open(raw)))
    cell = {}
    for r in rows:
        cell.setdefault((r["subject"], r["background"], int(r["k"])), []).append(r)
    def ms(v): return (st.mean(v), st.stdev(v) if len(v) > 1 else 0.0)
    lines = ["# Multi-flow coexistence (backend=%s). J = Jain index over all n=1+k flows; subj = subject share / fair share (1.00 = fair);"
             % (rows[0]["backend"] if rows else "?"),
             "# min = smallest flow's share / fair share (starvation indicator; <0.5 means some flow gets under half its fair share)",
             "subject,background,k,n,J_mean,J_sd,subj_mean,subj_sd,min_mean,min_sd"]
    tex = []
    for (s, b, k), rs in sorted(cell.items()):
        J = ms([float(r["J"]) for r in rs]); S = ms([float(r["subject_rel"]) for r in rs]); M = ms([float(r["min_rel"]) for r in rs])
        lines.append("%s,%s,%d,%d,%.3f,%.3f,%.2f,%.2f,%.2f,%.2f" % (s, b, k, len(rs), J[0], J[1], S[0], S[1], M[0], M[1]))
        tex.append("%s & %s & %d & %d & %.3f $\\pm$ %.3f & %.2f $\\pm$ %.2f & %.2f $\\pm$ %.2f \\\\" %
                   (s.replace("_", "\\_"), b.upper(), k, len(rs), J[0], J[1], S[0], S[1], M[0], M[1]))
    lines.append("")
    lines.append("# Welch tests, per background/k:  A vs B")
    for A, B in (("neural", "neural_polite"), ("smart_fair", "neural"), ("smart_fair", "neural_polite"), ("smart_fair", "smart_nofair")):
        for (s_, b, k), rs in sorted(cell.items()):
            if s_ != A: continue
            o = cell.get((B, b, k))
            if not o: continue
            for key, name in (("J", "Jain"), ("subject_rel", "subject share"), ("min_rel", "min share")):
                x = [float(r[key]) for r in rs]; y = [float(r[key]) for r in o]
                t = welch(x, y)
                lines.append("%s vs %s | %s k=%d %-13s %.3f vs %.3f  %s" % (A, B, b, k, name, st.mean(x), st.mean(y), ("t=%.2f p=%.3f" % t) if t else "n/a"))
    open(os.path.join(out, "summary.csv"), "w").write("\n".join(lines) + "\n")
    open(os.path.join(out, "tables.tex"), "w").write("\n".join(tex) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
