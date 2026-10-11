#!/usr/bin/env python3
"""RUDP Drop: send a file over RUDP across an emulated network and watch the congestion-control policies race.

Everything runs locally with the real C code: bin/server (receiver), bin/impair (delay / jitter / loss / bottleneck proxy)
and bin/smart_client (sender, linked against librudp). Python only starts the processes and serves the page.

    make smart impair && python3 apps/drop/app.py          then open http://localhost:8800
"""
import hashlib, json, os, re, subprocess, sys, tempfile, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
SRV_PORT, PX_PORT, WEB_PORT = 9961, 9962, int(os.environ.get("PORT", "8800"))
MAX_BYTES = 12 * 1000 * 1000
WORK = tempfile.mkdtemp(prefix="rudpdrop_")

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
    "deep":    dict(label="Neural agent",       args="--mode deep --init 10 --dfile results/dqn_chat.txt"),
}
jobs, lock, busy = {}, threading.Lock(), [False]

def build():
    os.makedirs(os.path.join(ROOT, "bin"), exist_ok=True)
    srv = os.path.join(ROOT, "bin", "server_drop")
    subprocess.check_call(["gcc", "-O2", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DSERVER_PORT=%d" % SRV_PORT, "-o", srv,
                           os.path.join(ROOT, "src/server.c"), os.path.join(ROOT, "src/common.c")], cwd=ROOT)
    for b in ("smart_client", "impair"):
        if not os.path.exists(os.path.join(ROOT, "bin", b)):
            subprocess.check_call(["make", "smart", "impair"], cwd=ROOT, stdout=subprocess.DEVNULL); break

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""): h.update(ch)
    return h.hexdigest()

def read_trace(path):
    pts = []
    try:
        for ln in open(path):
            p = ln.strip().split(",")
            if len(p) >= 3 and p[1] in ("ACK", "FAST_RETX", "TIMEOUT", "RTO"):
                try: pts.append((float(p[0]), float(p[2])))
                except ValueError: pass
    except OSError: pass
    if len(pts) > 240:
        step = len(pts) / 240.0; pts = [pts[int(i * step)] for i in range(240)]
    return pts

def run_job(job):
    try:
        for arm in job["arms"]:
            st = job["state"][arm]; st["status"] = "running"; st["t0"] = time.time()
            d = os.path.join(WORK, job["id"]); out = os.path.join(d, arm + ".out"); trace = os.path.join(d, arm + ".trace")
            log = open(os.path.join(d, arm + ".srv.log"), "w")
            env = dict(os.environ, RUDP_OUT=out, RUDP_QUIET="1")
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
                st.update(ms=float(f[3]), sent=int(f[5]), retx=int(f[6]), timeouts=int(f[7]), avg_rtt=float(f[9]))
                st["goodput_mb_s"] = job["size"] / 1e6 / (st["ms"] / 1000.0)
                try:
                    st["received"] = os.path.getsize(out); st["ok"] = (sha(out) == job["sha"])
                except OSError: st["ok"] = False
                st["status"] = "done" if st["ok"] else "corrupt"
            else:
                st["status"] = "failed"; st["error"] = st.get("error") or (txt.strip().splitlines() or ["no result"])[-1]
            st["trace"] = read_trace(trace); st["elapsed"] = time.time() - st["t0"]
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
            return self.send(200, dict(profiles=PROFILES, arms={k: v["label"] for k, v in ARMS.items()}, max_bytes=MAX_BYTES))
        m = re.match(r"/api/job/([0-9a-f]+)$", u)
        if m and m.group(1) in jobs:
            j = jobs[m.group(1)]
            return self.send(200, dict(id=j["id"], name=j["name"], size=j["size"], profile=j["profile"], arms=j["arms"], state=j["state"], finished=j["finished"]))
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
    print("RUDP Drop on http://localhost:%d  (work dir %s)" % (WEB_PORT, WORK))
    ThreadingHTTPServer(("127.0.0.1", WEB_PORT), H).serve_forever()
