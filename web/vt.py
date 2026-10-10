"""Helpers for the virtual-time C sender (bin/smart_client run with --vt 1).
The sender code is the same C code that runs on real sockets; only the clock and the receiver are simulated."""
import os, subprocess, math, statistics as st
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BIN = os.environ.get("VT_BIN") or os.path.join(ROOT, "bin", "smart_client")
DFILE = os.path.join(ROOT, "results", "dqn_chat.txt")
T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23, 15: 2.13, 20: 2.09, 30: 2.05, 40: 2.02, 60: 2.0}

PATHS = {   # name: (path args, packets)
    "5g":    ("--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60", 1200),
    "lossy": ("--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50", 1200),
    "sat":   ("--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100", 1200),
    "fast":  ("--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300", 3000),
    "bad":   ("--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40", 300),
}
ARMS = {
    "aimd_32":  "--mode aimd --init 10 --maxcwnd 32",
    "aimd_128": "--mode aimd --init 10 --maxcwnd 128 --ssthresh 128",
    "cubic_128": "--mode cubic --init 10 --maxcwnd 128 --ssthresh 128",
    "deep":     "--mode deep --init 10 --dfile " + DFILE,
}

def run(arm, path, seed, npk=None, dfile=None, extra="", env=None):
    pargs, n = PATHS[path] if path in PATHS else (path, npk or 1200)
    a = ARMS.get(arm, arm)
    if dfile: a = a.replace(DFILE, dfile)
    cmd = [BIN, "--vt", "1", *a.split(), *pargs.split(), *extra.split(), "--packets", str(npk or n), "--seed", str(seed)]
    out = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, **env) if env else None).stdout
    share = None
    for line in out.splitlines():
        if line.startswith("VTSHARE"): share = float(line.split(",")[3])
        if line.startswith("RESULT"):
            f = line.split(",")
            return dict(time=float(f[3]) / 1000, sent=int(f[5]), retx=int(f[6]), timeouts=int(f[7]), fast=int(f[8]), rtt=float(f[9]), share=share)
    return None

def sweep(arm, path, seeds, **kw):
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        return [r for r in ex.map(lambda s: run(arm, path, s, **kw), seeds) if r]

def mean_ci(xs):
    n = len(xs); m = st.mean(xs)
    if n < 2: return m, 0.0
    t = T95.get(n - 1) or T95.get(max(k for k in T95 if k <= n - 1), 1.96)
    return m, t * st.stdev(xs) / math.sqrt(n)
