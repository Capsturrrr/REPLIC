#!/usr/bin/env python3
"""Poise Drop: send a file over Poise across an emulated network and watch the congestion-control policies race.

Everything runs locally with the real C code: bin/server (receiver), bin/impair (delay / jitter / loss / bottleneck proxy)
and bin/smart_client (sender, linked against libpoise). Python only starts the processes and serves the page.

    python3 apps/drop/app.py          then open http://localhost:8800   (needs python3 and gcc; nothing else)
"""
import hashlib, json, os, re, subprocess, sys, tempfile, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
SRV_PORT, PX_PORT, WEB_PORT = 9961, 9962, int(os.environ.get("PORT", "8800"))
MAX_BYTES = 12 * 1000 * 1000
WORK = tempfile.mkdtemp(prefix="poisedrop_")

PROFILES = {
    "5g":    dict(label="5G",            note="10 ms one way, light loss, fast link",        args="--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60"),
    "lossy": dict(label="Lossy Wi-Fi",   note="25 ms, 10 ms jitter, 2% random loss",         args="--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50"),
    "sat":   dict(label="Satellite",     note="150 ms one way, long fat pipe",               args="--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100"),
    "fast":  dict(label="Long fast link", note="60 ms, very high bandwidth",                  args="--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300"),
    "bad":   dict(label="Terrible link", note="40 ms, 10% loss, narrow",                     args="--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40"),
}
ARMS = {
    "aimd32":  dict(label="AIMD, window 32",    args="--mode aimd --init 10 --maxcwnd 32"),
    "aimd128": dict(label="Tuned AIMD, 128",    args="--mode aimd --init 10 --maxcwnd 128 --ssthresh 128"),
    "deep":    dict(label="Neural agent (v1)",  args="--mode deep --init 10 --dfile results/dqn_chat.txt"),
    "smart":   dict(label="Poise (PPO)",   args="--mode smart --init 10"),     # weights compiled in from src/neural_weights.h
}
EXPLAIN = {
    "aimd32":  "Classic additive-increase, multiplicative-decrease. The window grows by one packet per round trip and halves on loss. Capped at 32 packets, so on a long fat link it can never fill the pipe.",
    "aimd128": "The same AIMD rule with the cap raised to 128 packets. Fairer to AIMD: it can use big pipes, but it still halves its window on every loss, even random loss that is not congestion.",
    "deep":    "Our first learned agent (DQN, Neural v1). It looks at recent loss and delay and picks from a few fixed window changes. Good on some links, brittle on others.",
    "smart":   "Poise, a small PPO-trained neural network (8 inputs, two layers of 32). Every 100 ms or so it reads RTT, loss and delivery rate, then scales the window by a factor between 0.5 and 2. A fairness term in training stops it from crowding out other flows.",
}
jobs, lock, busy = {}, threading.Lock(), [False]

def build():
    """Compile the three C programs with gcc if they are missing or older than their sources (no make needed)."""
    bindir = os.path.join(ROOT, "bin"); os.makedirs(bindir, exist_ok=True)
    src = lambda *n: [os.path.join(ROOT, "src", x) for x in n]
    cflags = ["-O2", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-D_GNU_SOURCE", "-w"]
    targets = {
        "server_drop":  (src("server.c", "common.c"), ["-DSERVER_PORT=%d" % SRV_PORT], []),
        "smart_client": (src("smart_client.c", "poise_sender.c", "common.c"), [], ["-lm"]),
        "impair":       (src("impair.c"), [], ["-lm"]),
    }
    for name, (files, defs, libs) in targets.items():
        out = os.path.join(bindir, name)
        deps = files + src("common.h", "poise.h", "rl_cc.h", "deep_cc.h", "smart_cc.h", "neural_weights.h")
        if os.path.exists(out) and all(os.path.getmtime(out) >= os.path.getmtime(f) for f in deps if os.path.exists(f)): continue
        print("compiling", name, flush=True)
        try: subprocess.check_call(["gcc"] + cflags + defs + ["-o", out] + files + libs, cwd=ROOT)
        except (OSError, subprocess.CalledProcessError) as e:
            sys.exit("Could not compile %s (%s). Install a C compiler: sudo apt install build-essential" % (name, e))
    if not os.path.exists(os.path.join(ROOT, "results", "dqn_chat.txt")): sys.exit("results/dqn_chat.txt (neural weights) is missing")

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""): h.update(ch)
    return h.hexdigest()

EVENT_LABEL = {"FAST_RETX": "fast retransmit", "SACK_RETX": "selective retransmit", "TIMEOUT": "timer expiry", "RTO": "timer expiry", "GUARD": "safety guard"}

def read_trace(path):
    """Return (points, events, summary). points = [ms, cwnd] thinned to about 240; events = loss/recovery markers; summary = phases."""
    pts, evs, first_ack, all_pts = [], [], None, []
    try:
        for ln in open(path):
            p = ln.strip().split(",")
            if len(p) < 4 or p[1] == "event": continue
            try: t, cw = float(p[0]), float(p[2])
            except ValueError: continue
            if p[1] in ("ACK", "FAST_RETX", "SACK_RETX", "TIMEOUT", "RTO", "SMART", "DONE") or p[1].startswith("RL_"):
                all_pts.append((t, cw, p[1]))
                if first_ack is None and p[1] == "ACK": first_ack = t
            if p[1] in EVENT_LABEL: evs.append((t, p[1], cw))
    except OSError: pass
    pts = [(t, c) for t, c, _ in all_pts]
    if len(pts) > 240:
        step = len(pts) / 240.0; pts = [pts[int(i * step)] for i in range(240)]
    summ = {}
    if all_pts:
        # slow start ends at the first loss/recovery event (the window stops doubling there)
        ss_end = next((t for t, k, c in evs), None)
        peak = max(all_pts, key=lambda x: x[1])
        summ = dict(first_ack_ms=first_ack, slow_start_end_ms=ss_end, peak_cwnd=peak[1], peak_at_ms=peak[0],
                    end_cwnd=all_pts[-1][1], min_after_peak=min([c for t, c, _ in all_pts if t >= peak[0]] or [peak[1]]))
        kinds = {}
        for _, k, _ in evs: kinds[EVENT_LABEL[k]] = kinds.get(EVENT_LABEL[k], 0) + 1
        summ["event_counts"] = kinds
    if len(evs) > 60:
        step = len(evs) / 60.0; evs = [evs[int(i * step)] for i in range(60)]
    return pts, [(t, EVENT_LABEL[k], c) for t, k, c in evs], summ

def link_math(profile, size):
    """What the emulated link allows, so the page can say how close a transfer came to the best possible."""
    a = PROFILES[profile]["args"].split(); g = lambda k, d: float(a[a.index(k) + 1]) if k in a else d
    delay, rate, queue, loss, jitter = g("--delay", 0), g("--rate", 0), g("--queue", 0), g("--loss", 0), g("--jitter", 0)
    pkts = -(-size // 1200); rtt = 2 * delay
    return dict(one_way_ms=delay, rtt_ms=rtt, jitter_ms=jitter, loss_pct=loss, rate_pps=rate, rate_mb_s=rate * 1200 / 1e6, queue=queue,
                bdp_pkts=rate * rtt / 1000.0, packets=pkts, best_s=(pkts / rate + rtt / 1000.0) if rate else None)

def run_job(job):
    try:
        for arm in job["arms"]:
            st = job["state"][arm]; st["status"] = "running"; st["t0"] = time.time()
            d = os.path.join(WORK, job["id"]); out = os.path.join(d, arm + ".out"); trace = os.path.join(d, arm + ".trace")
            log = open(os.path.join(d, arm + ".srv.log"), "w")
            env = dict(os.environ, POISE_OUT=out, POISE_QUIET="1")
            srv = subprocess.Popen([os.path.join(ROOT, "bin/server_drop")], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
            px = subprocess.Popen([os.path.join(ROOT, "bin/impair"), str(PX_PORT), str(SRV_PORT)] + PROFILES[job["profile"]]["args"].split(),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
            time.sleep(0.3)
            cmd = [os.path.join(ROOT, "bin/smart_client")] + ARMS[arm]["args"].split() + ["--emu", "0", "--port", str(PX_PORT), "--trace", trace, "--file", job["file"]]
            cl = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT)
            try:
                while cl.poll() is None:
                    try: st["received"] = os.path.getsize(out)
                    except OSError: pass
                    st["elapsed"] = time.time() - st["t0"]
                    if st["elapsed"] > 300: cl.kill(); st["error"] = "timed out"
                    time.sleep(0.15)
                txt = cl.stdout.read()
            finally:
                time.sleep(0.4)
                for p in (px, srv):
                    p.terminate()
                    try: p.wait(3)
                    except subprocess.TimeoutExpired: p.kill()
                log.close()
            m = [l for l in txt.splitlines() if l.startswith("RESULT")]
            if m and not st.get("error"):
                f = m[0].split(",")
                st.update(ms=float(f[3]), packets=int(f[4]), sent=int(f[5]), retx=int(f[6]), timeouts=int(f[7]), fast=int(f[8]), avg_rtt=float(f[9]), min_rtt=float(f[10]))
                st["goodput_mb_s"] = job["size"] / 1e6 / (st["ms"] / 1000.0)
                try:
                    st["received"] = os.path.getsize(out); st["ok"] = (sha(out) == job["sha"])
                except OSError: st["ok"] = False
                st["status"] = "done" if st["ok"] else "corrupt"
            else:
                st["status"] = "failed"; st["error"] = st.get("error") or (txt.strip().splitlines() or ["no result"])[-1]
            st["trace"], st["events"], st["phases"] = read_trace(trace); st["elapsed"] = time.time() - st["t0"]
    finally:
        job["finished"] = True
        with lock: busy[0] = False

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json", extra=None):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = self.path.split("?")[0]
        if u in ("/", "/index.html"):
            return self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        if u == "/api/config":
            return self.send(200, dict(profiles={k: dict(v, math=link_math(k, 1_000_000)) for k, v in PROFILES.items()}, arms={k: v["label"] for k, v in ARMS.items()},
                                   explain=EXPLAIN, max_bytes=MAX_BYTES))
        m = re.match(r"/api/job/([0-9a-f]+)$", u)
        if m and m.group(1) in jobs:
            j = jobs[m.group(1)]
            return self.send(200, dict(id=j["id"], name=j["name"], size=j["size"], profile=j["profile"], arms=j["arms"], state=j["state"], finished=j["finished"], link=link_math(j["profile"], j["size"])))
        m = re.match(r"/download/([0-9a-f]+)/(\w+)$", u)
        if m and m.group(1) in jobs and m.group(2) in ARMS:
            j = jobs[m.group(1)]; p = os.path.join(WORK, j["id"], m.group(2) + ".out")
            if os.path.exists(p) and j["state"].get(m.group(2), {}).get("ok"):
                return self.send(200, open(p, "rb").read(), "application/octet-stream", {"Content-Disposition": 'attachment; filename="%s"' % re.sub(r'[^\w.\-]', "_", j["name"])})
        self.send(404, {"error": "not found"})
    def do_POST(self):
        if self.path.split("?")[0] != "/api/start": return self.send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > MAX_BYTES: return self.send(413, {"error": "file must be between 1 byte and %d MB" % (MAX_BYTES // 1000000)})
        profile = self.headers.get("X-Profile", "lossy"); arms = [a for a in self.headers.get("X-Arms", "").split(",") if a in ARMS]
        if profile not in PROFILES or not arms: return self.send(400, {"error": "choose a network and at least one policy"})
        with lock:
            if busy[0]: return self.send(409, {"error": "a transfer is already running"})
            busy[0] = True
        jid = uuid.uuid4().hex[:10]; d = os.path.join(WORK, jid); os.makedirs(d)
        path = os.path.join(d, "input.bin"); left = n
        with open(path, "wb") as f:
            while left > 0:
                ch = self.rfile.read(min(65536, left))
                if not ch: break
                f.write(ch); left -= len(ch)
        name = os.path.basename(self.headers.get("X-Filename", "file.bin")) or "file.bin"
        job = dict(id=jid, name=name, file=path, size=n, sha=sha(path), profile=profile, arms=arms, finished=False,
                   state={a: dict(status="queued", received=0, elapsed=0) for a in arms})
        jobs[jid] = job; threading.Thread(target=run_job, args=(job,), daemon=True).start()
        self.send(200, dict(id=jid))

if __name__ == "__main__":
    build()
    print("Poise Drop on http://localhost:%d  (work dir %s)" % (WEB_PORT, WORK))
    host = os.environ.get("HOST", "127.0.0.1")
    if host != "127.0.0.1": print("WARNING: listening on %s; anyone who can reach this port can upload files and start transfers" % host)
    try: ThreadingHTTPServer((host, WEB_PORT), H).serve_forever()
    except OSError as e: sys.exit("Cannot listen on port %d (%s). Set PORT=8801 to use another." % (WEB_PORT, e))
