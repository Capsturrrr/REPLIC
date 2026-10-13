#!/usr/bin/env python3
"""
run_all_tests.py -- one-command experimental matrix for RUDP / Poise (v2).

    python3 run_all_tests.py --quick            # about 2 minutes: 3 scenarios, 6 policies, 2 reps, 256 kB
    python3 run_all_tests.py                    # full matrix (see MATRIX below), 5 reps, 100 kB / 1 MB / 8 MB
    python3 run_all_tests.py --backend netem    # needs root + tc; real kernel queues on loopback (also runs kernel TCP)

What it does: builds the C binaries, runs every (scenario x policy x size x repetition) cell, checks the
delivered file byte for byte, writes raw rows and summary statistics (mean, median, p95, p99 of completion time
and goodput), and generates LaTeX tables plus vector-PDF figures under results/orch/.

Backends
  proxy  (default, no root)  bin/impair: a UDP proxy that adds delay, uniform jitter, random forward loss and a
                             drop-tail rate-limited bottleneck. ACKs are neither lost nor rate-limited.
  netem  (root, Linux)       tc netem + tbf on lo with MTU 1500, and kernel TCP as an extra policy. NOT exercised in
                             the sandbox where this file was written (no tc); treat as untested until you run it.

Honest limits (also printed in the report header)
  * jitter is uniform in [-J, +J], not Gaussian, and the proxy never reorders (arrivals are clamped to be monotone);
  * one RUDP transfer is limited to MAXN*MAX_PAYLOAD bytes (about 78 MB), so 1 GB is refused, not faked;
  * the neural agent in the matrix is the weights in results/dqn_chat.txt (trained on 512 B-packet simulator paths).
"""
import argparse, csv, hashlib, itertools, math, os, shutil, statistics, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "results", "orch")
PAYLOAD = 1200                      # MAX_PAYLOAD in src/common.h
WIRE = PAYLOAD + 13                 # bytes on the wire per full data packet
SRV_PORT, PX_PORT = 9961, 9962
MAXBYTES = 65536 * PAYLOAD          # sender MAXN * MAX_PAYLOAD

def load_profiles(path=None):
    """Single source of truth shared with setup_netem_benchmarks.sh: netem_profiles.tsv"""
    path = path or os.path.join(ROOT, "netem_profiles.tsv"); rows = []
    with open(path) as f:
        hdr = f.readline().rstrip("\n").split("\t")
        for ln in f:
            v = ln.rstrip("\n").split("\t")
            if len(v) < len(hdr): continue
            d = dict(zip(hdr, v))
            rows.append(dict(name=d["name"], delay=float(d["delay_ms"]), jitter=float(d["jitter_ms"]), loss=float(d["loss_pct"]),
                             rate=float(d["rate_kBps"]), queue=float(d["queue_kB"]), note=d["note"]))
    return rows

MATRIX = load_profiles()
QUICK = ["clean", "loss2_j10", "loss10_j35"]
KERNEL_TCP = "tcp"          # extra policy, only with the netem backend (kernel TCP cannot traverse the UDP proxy): system default congestion control
TCP_VARIANTS = {"tcp": None, "tcp_cubic": "cubic", "tcp_bbr": "bbr"}      # policy name -> TCP_CONGESTION value set on both sockets
TCP_POLS = list(TCP_VARIANTS)
def is_tcp(pol): return pol in TCP_VARIANTS


def tcp_cc_available():
    """Congestion-control algorithms the running kernel offers (empty set when /proc is unreadable)."""
    try: return set(open("/proc/sys/net/ipv4/tcp_available_congestion_control").read().split())
    except OSError: return set()

# policy name -> (client args, FEC group size or 0)
POLICIES = {
    "aimd":      ("--mode aimd --maxcwnd 128 --ssthresh 128 --init 10", 0),
    "aimd32":    ("--mode aimd --maxcwnd 32 --init 10", 0),   # original (untuned) AIMD: window capped at 32
    "cubic":     ("--mode cubic --maxcwnd 128 --ssthresh 128 --init 10", 0),
    "bbr":       ("--mode bbr", 0),
    "qtable":    ("--mode rl --qfile results/q_lossy.txt", 0),
    "neural":    ("--mode deep --init 10 --dfile results/dqn_chat.txt", 0),
    "smart":     ("--mode smart --init 10", 0),            # Poise (PPO, weights compiled in from src/neural_weights.h)
    "smart+fec": ("--mode smart --init 10", 8),
    "aimd+fec":  ("--mode aimd --maxcwnd 128 --ssthresh 128 --init 10", 8),
    "neural+fec": ("--mode deep --init 10 --dfile results/dqn_chat.txt", 8),
}


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, cwd=ROOT, **kw)


def build():
    cf = "-O2 -std=c11 -D_POSIX_C_SOURCE=200809L -D_GNU_SOURCE -w"
    os.makedirs(os.path.join(ROOT, "bin"), exist_ok=True)
    for cmd in (f"gcc {cf} -DSERVER_PORT={SRV_PORT} -o bin/server_drop src/server.c src/common.c",
                f"gcc {cf} -o bin/smart_client src/smart_client.c src/rudp_sender.c src/common.c -lm",
                f"gcc {cf} -o bin/impair src/impair.c -lm"):
        if sh(cmd).returncode:
            sys.exit("build failed: " + cmd)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""):
            h.update(ch)
    return h.hexdigest()



# ------------------------------------------------------------------ netem backend + kernel TCP ------------------------------------------------------------------
def netem_cmds(sc, dist=True):
    """tc commands for one scenario on the loopback device. netem on lo delays/drops BOTH directions (so RTT = 2 x delay and ACKs can be lost),
    unlike the UDP proxy, which impairs the forward path only; a tbf stage after netem gives the rate-limited drop-tail bottleneck."""
    d, j, l = sc["delay"], sc["jitter"], sc["loss"]
    jit = (f" {j:g}ms" + (" distribution normal" if dist else "")) if j > 0 else ""
    nm = f"netem delay {d:g}ms{jit} loss {l:g}% limit 100000"
    return ["ip link set dev lo mtu 1500", "tc qdisc del dev lo root",
            f"tc qdisc add dev lo root handle 1: {nm}",
            f"tc qdisc add dev lo parent 1:1 handle 10: tbf rate {int(sc['rate'] * 8)}kbit burst 16kb limit {int(sc['queue'] * 1000 + 16000)}"]


def netem_apply(sc, dist=True):
    for k, c in enumerate(netem_cmds(sc, dist)):
        r = subprocess.run(c, shell=True, capture_output=True, text=True)
        if r.returncode and k != 1:                       # command 1 (qdisc del) is allowed to fail when nothing is installed
            raise RuntimeError(f"{c!r} failed: {r.stderr.strip()}")


def netem_clear(mtu=None):
    subprocess.run("tc qdisc del dev lo root", shell=True, capture_output=True)
    if mtu: subprocess.run(f"ip link set dev lo mtu {mtu}", shell=True, capture_output=True)


def probe_netem():
    """Returns (ok, info). info is the reason when not ok, else a dict(dist=bool, mtu=original lo mtu)."""
    if not shutil.which("tc") or not shutil.which("ip"): return False, "tc/ip not installed (apt install iproute2)"
    if not hasattr(os, "geteuid") or os.geteuid() != 0: return False, "not root (re-run with sudo)"
    try: mtu = open("/sys/class/net/lo/mtu").read().strip()
    except OSError: mtu = "65536"
    def tryadd(extra):
        subprocess.run("tc qdisc del dev lo root", shell=True, capture_output=True)
        r = subprocess.run(f"tc qdisc add dev lo root netem delay 1ms{extra}", shell=True, capture_output=True, text=True)
        subprocess.run("tc qdisc del dev lo root", shell=True, capture_output=True)
        return r
    r = tryadd(" 1ms distribution normal")
    if r.returncode == 0: return True, dict(dist=True, mtu=mtu)
    r2 = tryadd("")
    if r2.returncode == 0: return True, dict(dist=False, mtu=mtu)        # netem works but the normal-distribution table is missing: uniform jitter
    return False, "netem not usable: " + (r2.stderr.strip() or r.stderr.strip() or "unknown error") + " (WSL2 needs a kernel with sch_netem)"


TCP_PORT = 9971
def tcp_retrans():
    try:
        with open("/proc/net/snmp") as f: rows = [ln.split() for ln in f if ln.startswith("Tcp:")]
        return int(rows[1][rows[0].index("RetransSegs")])
    except Exception:
        return None


def run_tcp(sc, size, rep, pol=KERNEL_TCP):
    """Kernel TCP baseline: same bytes, same bottleneck. Timer starts after connect() (RUDP's timer also starts after its handshake) and stops when
    the receiver has all bytes and says so. Retransmissions = system-wide Tcp RetransSegs delta (only meaningful on an otherwise idle machine)."""
    import socket, threading
    work = os.path.join(OUT, "work"); os.makedirs(work, exist_ok=True)
    src = os.path.join(work, f"in_{size}.bin")
    if not os.path.exists(src):
        with open(src, "wb") as f: f.write(os.urandom(size))
    data = open(src, "rb").read(); want = hashlib.sha256(data).hexdigest(); got = {}
    cc = TCP_VARIANTS.get(pol); tmo = max(60, size / 5000.0)           # same give-up time as the RUDP client
    row = dict(scenario=sc["name"], policy=pol, size=size, rep=rep, ok=0, fail="", ms="", goodput_MBps="", sent="", retx="", timeouts="")
    if cc and cc not in tcp_cc_available():
        row["fail"] = f"kernel has no tcp_{cc} (modprobe tcp_{cc})"; return row
    ls = socket.socket(); ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); ls.bind(("127.0.0.1", TCP_PORT)); ls.listen(1)
    if cc: ls.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, cc.encode())     # inherited by the accepted socket (the receiver never sends bulk data, but keep both ends identical)
    def sink():
        c, _ = ls.accept(); h = hashlib.sha256(); n = 0
        while n < size:
            b = c.recv(1 << 16)
            if not b: break
            h.update(b); n += len(b)
        got["sha"] = h.hexdigest(); got["n"] = n
        try: c.sendall(b"K")
        except OSError: pass
        c.close()
    th = threading.Thread(target=sink, daemon=True); th.start()
    c = socket.socket(); c.settimeout(tmo)
    if cc: c.setsockopt(socket.IPPROTO_TCP, socket.TCP_CONGESTION, cc.encode())
    c.connect(("127.0.0.1", TCP_PORT))
    r0 = tcp_retrans(); t0 = time.perf_counter()
    try:
        c.sendall(data); c.settimeout(tmo); c.recv(1)
        ms = (time.perf_counter() - t0) * 1000.0; r1 = tcp_retrans()
        th.join(5)
        row.update(ms=ms, goodput_MBps=f"{size / ms / 1000.0:.4f}", retx="" if r0 is None or r1 is None else r1 - r0)
        row["ok"] = int(got.get("sha") == want)
        if not row["ok"]: row["fail"] = f"tcp received {got.get('n')} of {size} bytes"
    except OSError as e:
        row["fail"] = f"tcp error {e}"
    finally:
        c.close(); ls.close()
    return row


def run_cell(sc, pol, size, rep, backend, tag):
    if is_tcp(pol): return run_tcp(sc, size, rep, pol)
    args, fec = POLICIES[pol]
    work = os.path.join(OUT, "work"); os.makedirs(work, exist_ok=True)
    src = os.path.join(work, f"in_{size}.bin")
    if not os.path.exists(src):
        with open(src, "wb") as f: f.write(os.urandom(size))
    dst = os.path.join(work, "out.bin")
    if os.path.exists(dst): os.remove(dst)
    trace = os.path.join(OUT, "traces", f"{tag}.csv"); os.makedirs(os.path.dirname(trace), exist_ok=True)
    env = dict(os.environ, RUDP_OUT=dst, RUDP_QUIET="1", RUDP_FEC=str(fec))
    slog = os.path.join(work, "srv.log")
    srv = subprocess.Popen([os.path.join(ROOT, "bin/server_drop")], env=env, cwd=ROOT, stdout=open(slog, "w"), stderr=subprocess.STDOUT)
    px = None
    port = SRV_PORT
    if backend == "proxy":
        pps = max(1.0, sc["rate"] * 1000.0 / WIRE); q = max(4, int(sc["queue"] * 1000.0 / WIRE))
        px = subprocess.Popen([os.path.join(ROOT, "bin/impair"), str(PX_PORT), str(SRV_PORT), "--delay", str(sc["delay"]),
                               "--jitter", str(sc["jitter"]), "--loss", str(sc["loss"]), "--rate", f"{pps:.1f}", "--queue", str(q),
                               "--seed", str(sc.get("seed", 1000 + rep))] + (["--cross", sc["cross"]] if sc.get("cross") else []) + (["--rloss", str(sc["loss"])] if os.environ.get("IMPAIR_NETEMLIKE") else []), cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = PX_PORT
    time.sleep(0.3)
    cmd = [os.path.join(ROOT, "bin/smart_client")] + args.split() + ["--emu", "0", "--port", str(port), "--trace", trace, "--file", src]
    row = dict(scenario=sc["name"], policy=pol, size=size, rep=rep, ok=0, fail="", ms="", goodput_MBps="", sent="", retx="", timeouts="")
    for old in __import__("glob").glob(dst + ".*"): os.remove(old)
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=max(60, size / 5000.0))
        res = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT")]
        if res:
            f = res[-1].split(",")
            # RESULT,scenario,mode,ms,packets,sent,retx,timeouts,fast,avg_rtt,min_rtt,pps,cwnd,reward
            ms = float(f[3]); row.update(ms=ms, sent=f[5], retx=f[6], timeouts=f[7], goodput_MBps=f"{size / ms / 1000.0:.4f}")
            for _ in range(60):                       # wait for the server to flush and close the file
                if os.path.exists(dst) and os.path.getsize(dst) == size: break
                time.sleep(0.1)
            row["ok"] = int(os.path.exists(dst) and sha(dst) == sha(src))
            if not row["ok"]:
                import glob
                shutil.copy(slog, os.path.join(OUT, f"fail_{tag}.srv.log"))
                row["fail"] = f"out_size={os.path.getsize(dst) if os.path.exists(dst) else -1} expected={size} other_out_files={len(glob.glob(dst + '.*'))}"
    except subprocess.TimeoutExpired:
        pass
    finally:
        for p in (srv, px):
            if p: p.terminate()
        for p in (srv, px):
            if p: p.wait()
    return row


def pct(xs, p):
    xs = sorted(xs)
    if not xs: return float("nan")
    k = (len(xs) - 1) * p / 100.0; lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(rows):
    cells = {}
    for r in rows:
        cells.setdefault((r["scenario"], r["size"], r["policy"]), []).append(r)
    out = []
    for (sc, size, pol), rs in cells.items():
        good = [r for r in rs if r["ok"] and r["ms"] != ""]
        ms = [float(r["ms"]) for r in good]; gp = [float(r["goodput_MBps"]) for r in good]
        out.append(dict(scenario=sc, size=size, policy=pol, n=len(rs), n_ok=len(good),
                        ms_mean=statistics.mean(ms) if ms else float("nan"), ms_median=statistics.median(ms) if ms else float("nan"),
                        ms_p95=pct(ms, 95), ms_p99=pct(ms, 99),
                        gp_mean=statistics.mean(gp) if gp else float("nan"), gp_p95=pct(gp, 95),
                        ms_sd=statistics.stdev(ms) if len(ms) > 1 else float("nan")))
    return out



def welch(a, b):
    """Welch t-test -> (t, p, df); None if scipy is missing or a sample is too small."""
    try:
        from scipy import stats
    except Exception:
        return None
    if len(a) < 2 or len(b) < 2: return None
    r = stats.ttest_ind(a, b, equal_var=False)
    return float(r.statistic), float(r.pvalue), float(getattr(r, "df", float("nan")))


def significance_report(rows):
    """For every (scenario, size) where X and X+fec both exist, print the mean change and Welch p; written to significance.txt."""
    out = []
    keyed = {}
    for r in rows:
        if r["ok"] in (1, "1") and r["ms"] != "": keyed.setdefault((r["scenario"], int(r["size"]), r["policy"]), []).append(float(r["ms"]))
    for (sc, size, pol), xs in sorted(keyed.items()):
        if pol.endswith("+fec") and (sc, size, pol[:-4]) in keyed:
            base = keyed[(sc, size, pol[:-4])]; w = welch(base, xs)
            line = f"{sc:12s} {size:>8d} {pol[:-4]:7s} -> +fec: {statistics.mean(base):8.0f} -> {statistics.mean(xs):8.0f} ms (time {100 * (statistics.mean(xs) / statistics.mean(base) - 1):+.0f}%), n={len(base)}/{len(xs)}"
            if w: line += f", Welch t={w[0]:.2f} p={w[1]:.4f}" + ("" if w[1] < 0.05 else " (not significant)")
            out.append(line)
        for tp in TCP_POLS:
            if not is_tcp(pol) and (sc, size, tp) in keyed:
                w = welch(keyed[(sc, size, tp)], xs)
                out.append(f"{sc:12s} {size:>8d} {pol:11s} vs kernel {tp}: x{statistics.mean(xs) / statistics.mean(keyed[(sc, size, tp)]):.2f} the time of TCP" + (f", Welch p={w[1]:.4f}" if w else ""))
    return out


def tex_escape(s): return s.replace("_", r"\_").replace("%", r"\%").replace("+", "+")


def write_tex(summ, path, backend_note=''):
    sizes = sorted({s["size"] for s in summ}); scs = []
    for s in summ:
        if s["scenario"] not in scs: scs.append(s["scenario"])
    pols = [p for p in (TCP_POLS + list(POLICIES)) if any(s["policy"] == p for s in summ)]
    with open(path, "w") as f:
        f.write("% auto-generated by run_all_tests.py; values are completion time in ms (mean / median / p95 / p99), n_ok runs\n")
        f.write(f"% backend: {backend_note}\n% percentiles from few runs are order statistics of a small sample (p95/p99 ~ the maximum when n < 20)\n")
        for size in sizes:
            f.write(f"\\begin{{table*}}[t]\\centering\\small\n\\caption{{Completion time (ms), {size // 1000} kB transfer. Mean / median / p95 / p99.}}\n")
            f.write("\\begin{tabular}{l" + "r" * len(pols) + "}\\toprule\nScenario & " + " & ".join(tex_escape(p) for p in pols) + " \\\\\\midrule\n")
            for sc in scs:
                cells = []
                for p in pols:
                    m = [s for s in summ if s["scenario"] == sc and s["policy"] == p and s["size"] == size]
                    if not m or m[0]["n_ok"] == 0: cells.append("--")
                    else:
                        m = m[0]; cells.append(f"{m['ms_mean']:.0f}/{m['ms_median']:.0f}/{m['ms_p95']:.0f}/{m['ms_p99']:.0f}" + ("" if m["n_ok"] == m["n"] else f"$^{{{m['n_ok']}/{m['n']}}}$"))
                f.write(tex_escape(sc) + " & " + " & ".join(cells) + " \\\\\n")
            f.write("\\bottomrule\\end{tabular}\n\\end{table*}\n\n")


def figures(summ, rows):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    sizes = sorted({s["size"] for s in summ}); size = sizes[-1] if len(sizes) == 1 else sizes[len(sizes) // 2]
    scs = []
    for s in summ:
        if s["scenario"] not in scs: scs.append(s["scenario"])
    pols = [p for p in (TCP_POLS + list(POLICIES)) if any(s["policy"] == p for s in summ)]
    fig, ax = plt.subplots(figsize=(7.0, 2.6)); w = 0.8 / max(1, len(pols))
    for k, p in enumerate(pols):
        ys, es = [], []
        for sc in scs:
            m = [s for s in summ if s["scenario"] == sc and s["policy"] == p and s["size"] == size]
            ys.append(m[0]["ms_mean"] if m and m[0]["n_ok"] else 0); es.append(m[0]["ms_sd"] if m and m[0]["n_ok"] > 1 else 0)
        ax.bar([i + k * w for i in range(len(scs))], ys, w, yerr=es, label=p, capsize=1.5, linewidth=0)
    ax.set_xticks([i + 0.4 - w / 2 for i in range(len(scs))]); ax.set_xticklabels(scs, rotation=20, ha="right")
    ax.set_ylabel("completion time (ms)"); ax.set_title(f"{size // 1000} kB transfer, mean $\\pm$ s.d.", fontsize=8); ax.legend(ncol=4, fontsize=6, frameon=False)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig_completion.pdf")); fig.savefig(os.path.join(OUT, "fig_completion.svg")); plt.close(fig)


def telemetry_figure(trace_csv, out_prefix, title, pkt_bytes=PAYLOAD):
    """Time-aligned multi-panel figure from a client trace (time_ms,event,cwnd,rtt_ms): cwnd with BDP estimate, RTT / SRTT / RTO bound,
    event markers, and windowed goodput. All panels share one x axis in seconds."""
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ev = []
    with open(trace_csv) as f:
        next(f)
        for ln in f:
            p = ln.strip().split(",")
            if len(p) >= 4:
                try: ev.append((float(p[0]) / 1000.0, p[1], float(p[2]), float(p[3])))
                except ValueError: pass
    if len(ev) < 5: return False
    ack = [e for e in ev if e[1] == "ACK"]
    t = [e[0] for e in ack]; cw = [e[2] for e in ack]; rtt = [e[3] for e in ack]
    minrtt = min([r for r in rtt if r > 0] or [1.0]); srtt = []; s = None
    for r in rtt:
        s = r if s is None else 0.875 * s + 0.125 * r; srtt.append(s)
    # RTO bound: SRTT + 4*RTTVAR with the same smoothing as the sender (alpha 1/8, beta 1/4)
    var = 0.0; rto = []; s2 = None
    for r in rtt:
        if s2 is None: s2, var = r, r / 2
        else: var = 0.75 * var + 0.25 * abs(s2 - r); s2 = 0.875 * s2 + 0.125 * r
        rto.append(s2 + 4 * var)
    # goodput: cumulative ACK events times payload over 100 ms windows (events are ACKs of new data, so this is a lower-bound proxy)
    win = 0.1; tmax = t[-1]; bins = [0] * (int(tmax / win) + 1)
    for x in t: bins[int(x / win)] += pkt_bytes
    gp = [b * 8 / win / 1e6 for b in bins]; gt = [(i + 0.5) * win for i in range(len(bins))]
    plt.rcParams.update({"font.family": "serif", "font.size": 8, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42})
    fig, axs = plt.subplots(4, 1, figsize=(3.5, 5.6), sharex=True)
    axs[0].step(t, cw, where="post", lw=0.8, label="cwnd"); axs[0].axhline(0, lw=0)
    axs[0].set_ylabel("cwnd (pkts)")
    # BDP proxy: max delivered rate in packets per min-RTT (see paper section on estimator limits)
    axs[1].plot(t, rtt, lw=0.4, alpha=0.5, label="RTT sample"); axs[1].plot(t, srtt, lw=0.9, label="SRTT"); axs[1].plot(t, rto, lw=0.9, ls="--", label="SRTT+4RTTVAR")
    axs[1].axhline(minrtt, lw=0.5, color="k", ls=":"); axs[1].set_ylabel("ms"); axs[1].legend(fontsize=6, frameon=False, ncol=3)
    for kind, mk in (("TIMEOUT", "x"), ("FAST_RETX", "v")):
        xs = [e[0] for e in ev if e[1] == kind]
        axs[2].plot(xs, [1] * len(xs), mk, ms=3, lw=0, label=kind.lower())
    axs[2].set_yticks([]); axs[2].set_ylabel("loss events"); axs[2].legend(fontsize=6, frameon=False, ncol=2)
    axs[3].step(gt, gp, where="mid", lw=0.8); axs[3].set_ylabel("goodput (Mb/s)"); axs[3].set_xlabel("time (s)")
    axs[0].set_title(title, fontsize=8)
    fig.tight_layout(); fig.savefig(out_prefix + ".pdf"); fig.savefig(out_prefix + ".svg"); plt.close(fig)
    return True


def read_rows(paths):
    rows = []
    for p in paths:
        with open(p) as f:
            for r in csv.DictReader(f):
                r["size"] = int(r["size"]); r["rep"] = int(r["rep"]); r["ok"] = int(r["ok"] or 0); rows.append(r)
    return rows


def emit(rows, backend_note, scs_names=None):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "raw.csv"), "w", newline="") as f:
        names = list(rows[0].keys())
        for r in rows:
            for k in r:
                if k not in names: names.append(k)
        w = csv.DictWriter(f, fieldnames=names); w.writeheader(); w.writerows(rows)
    summ = summarize(rows)
    with open(os.path.join(OUT, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0].keys())); w.writeheader(); w.writerows(summ)
    write_tex(summ, os.path.join(OUT, "tables.tex"), backend_note); figures(summ, rows)
    rep = significance_report(rows)
    with open(os.path.join(OUT, "significance.txt"), "w") as f: f.write(f"backend: {backend_note}\n" + "\n".join(rep) + "\n")
    return summ, rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true"); ap.add_argument("--backend", choices=["proxy", "netem"], default="proxy")
    ap.add_argument("--reps", type=int); ap.add_argument("--sizes", help="comma list of bytes, e.g. 100000,1000000")
    ap.add_argument("--policies", help="comma list from: " + ",".join(TCP_POLS + list(POLICIES)) + " (tcp* need --backend netem)")
    ap.add_argument("--scenarios", help="comma list of scenario names from netem_profiles.tsv")
    ap.add_argument("--out", help="output directory (default results/orch; use another one so a netem run does not overwrite the committed proxy results)")
    ap.add_argument("--netem-dry-run", action="store_true", help="print the tc commands for every selected scenario and exit (no root needed)")
    ap.add_argument("--merge", nargs="+", metavar="RAW_CSV", help="do not run anything: merge raw.csv files (e.g. a WSL netem run with a proxy run) and regenerate tables, figures and significance")
    a = ap.parse_args()
    if a.out:
        global OUT
        OUT = os.path.abspath(a.out)
    scs = [s for s in MATRIX if (not a.scenarios or s["name"] in a.scenarios.split(",")) and (not a.quick or a.scenarios or s["name"] in QUICK)]
    if a.netem_dry_run:
        for sc in scs:
            print(f"# {sc['name']}: {sc['note']}"); print("\n".join("  " + c for c in netem_cmds(sc)))
        return
    if a.merge:
        rows = read_rows(a.merge); summ, rep = emit(rows, "merged from " + ", ".join(a.merge)); print("\n".join(rep)); print(f"merged {len(rows)} rows -> {OUT}"); return
    sizes = [int(x) for x in a.sizes.split(",")] if a.sizes else ([256000] if a.quick else [100000, 1000000, 8000000])
    for s in sizes:
        if s > MAXBYTES: sys.exit(f"{s} bytes exceeds the sender limit of {MAXBYTES} bytes per transfer (rejected, not simulated or faked)")
    reps = a.reps or (2 if a.quick else 5)
    pols = a.policies.split(",") if a.policies else (["tcp_cubic", "tcp_bbr"] if a.backend == "netem" else []) + list(POLICIES)
    backend, note, dist, mtu = a.backend, "UDP impairment proxy (forward-path loss, uniform jitter)", True, None
    if a.backend == "netem":
        ok, info = probe_netem()
        if ok:
            dist, mtu = info["dist"], info["mtu"]
            note = "Linux tc netem + tbf on lo (both directions impaired), kernel TCP included" + ("" if dist else "; jitter UNIFORM (no normal.dist table)")
            import atexit; atexit.register(netem_clear, mtu)
        else:
            print(f"FALLBACK to the UDP proxy backend: {info}", flush=True)
            backend = "proxy"; note = f"UDP impairment proxy (FALLBACK: {info}); kernel TCP NOT run"
    if backend == "proxy" and any(is_tcp(p) for p in pols):
        print("note: kernel TCP needs the netem backend; skipping tcp policies", flush=True); pols = [p for p in pols if not is_tcp(p)]
    if backend == "netem":
        miss = [p for p in pols if is_tcp(p) and TCP_VARIANTS[p] and TCP_VARIANTS[p] not in tcp_cc_available()]
        if miss: print(f"WARNING: kernel lacks congestion control for {miss}; they are skipped (try: sudo modprobe tcp_bbr)", flush=True); pols = [p for p in pols if p not in miss]
    build(); os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "BACKEND.txt"), "w") as f: f.write(note + "\n")
    print("backend:", note, flush=True)
    rows = []; total = len(scs) * len(pols) * len(sizes) * reps; n = 0; t0 = time.time(); cur = None
    for sc, size, pol, rep in itertools.product(scs, sizes, pols, range(reps)):
        if backend == "netem" and cur != sc["name"]:
            netem_apply(sc, dist); cur = sc["name"]
        n += 1; tag = f"{sc['name']}_{pol.replace('+', 'p')}_{size}_{rep}"
        r = run_cell(sc, pol, size, rep, backend, tag); r["backend"] = backend; rows.append(r)
        print(f"[{n}/{total}] {sc['name']:12s} {pol:10s} {size:>8d} rep{rep} ok={r['ok']} ms={r['ms']}", flush=True)
    summ, rep_lines = emit(rows, note)
    for sc in scs:
        for pol in ("aimd", "neural", "neural+fec"):
            tr = os.path.join(OUT, "traces", f"{sc['name']}_{pol.replace('+', 'p')}_{sizes[-1]}_0.csv")
            if os.path.exists(tr): telemetry_figure(tr, os.path.join(OUT, f"telemetry_{sc['name']}_{pol.replace('+', 'p')}"), f"{sc['name']} / {pol}")
    bad = [r for r in rows if not r["ok"]]
    print("\n".join(rep_lines))
    print(f"\ndone in {time.time() - t0:.0f}s: {len(rows) - len(bad)}/{len(rows)} runs byte-identical; backend = {backend}; outputs in {OUT}")
    if bad: print("NOT OK:", [(r["scenario"], r["policy"], r["rep"]) for r in bad])


if __name__ == "__main__":
    main()
