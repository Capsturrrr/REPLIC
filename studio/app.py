#!/usr/bin/env python3
"""
Poise-RUDP Studio - backend.

FastAPI app that wraps the real C core (src/*.c, built into studio/bin) and the impairment proxy. Nothing here is
simulated in Python: every race starts real server / proxy / sender processes over UDP on 127.0.0.1, and the numbers
the browser shows are read from what those processes write.

  GET  /                   dashboard (templates/index.html)
  GET  /api/config         arms, defaults, slider limits
  POST /api/coexist        start the coexistence arena {subjects[], bg: cubic|bbr, k: 1..3, path sliders}  (shared bottleneck, live Jain)
  POST /api/race           start a race {loss, jitter, delay, rate_pps, queue, packets, arms[]}   -> {id}
  GET  /api/race/{id}      state + final results (completion time, retransmissions, ...)
  POST /api/stop           kill the running race
  GET  /api/fairness       results/fairness/summary.csv (Jain study) if it exists
  WS   /ws                 telemetry every 100 ms: t, per-arm policy, cwnd, rtt, loss rate, goodput, FEC state,
                           and the newest dissected packets (seq, ack, flags, CRC-16, CRC check, length)

Telemetry sources (all from the C processes):
  cwnd, rtt     <- sender --trace file (RUDP_LIVE=1 makes the sender flush each line)
  loss rate     <- share of TIMEOUT / FAST_RETX events among the last 1 s of trace events
  goodput       <- highest cumulative ACK seen in the sender's --pcap file, differentiated over 500 ms
  packets       <- the same pcap file, parsed with the RUDP header layout (13 bytes, big endian)
  FEC state     <- fixed XOR parity group size K (RUDP_FEC); parity packets counted in the pcap. K is NOT adaptive.

Run:  python3 studio/app.py [--port 8000]
"""
import asyncio, collections, json, os, re, struct, subprocess, sys, tempfile, time, uuid, argparse, csv

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(HERE, "bin")
WEIGHTS = os.path.join(ROOT, "results", "dqn_chat.txt")
PAYLOAD = 1200
SRV0, PRX0 = 9960, 9980          # arm i: server 9960+i, proxy 9980+i

SMART_TRUE = os.path.join(ROOT, "results", "smart", "smart_final.txt")
ARMS = {
    "smart_true": dict(label="Poise (PPO, fairness-trained)", flags="--mode smart --init 10 --dfile %s" % SMART_TRUE, fec=0, color="#22d3ee"),
    "smart_true_fec": dict(label="Poise + FEC", flags="--mode smart --init 10 --dfile %s" % SMART_TRUE, fec=8, color="#34d399"),
    "smart_fec": dict(label="Neural v1 (static DQN) + FEC", flags="--mode deep --init 10 --dfile %s" % WEIGHTS, fec=8, color="#2dd4bf"),
    "smart":     dict(label="Neural v1 (static DQN)", flags="--mode deep --init 10 --dfile %s" % WEIGHTS, fec=0, color="#60a5fa"),
    "smart_polite": dict(label="Neural v1 + --polite guard", flags="--mode deep --init 10 --polite 1.3 --dfile %s" % WEIGHTS, fec=0, color="#a78bfa"),
    "aimd":      dict(label="AIMD (tuned)",     flags="--mode aimd --init 10 --maxcwnd 128 --ssthresh 128", fec=0, color="#f87171"),
    "cubic":     dict(label="CUBIC",            flags="--mode cubic --init 10 --maxcwnd 128 --ssthresh 128", fec=0, color="#fbbf24"),
    "bbr":       dict(label="BBR-style",        flags="--mode bbr --init 10 --maxcwnd 128 --ssthresh 128", fec=0, color="#f472b6"),
}
BG_COLORS = ["#94a3b8", "#cbd5e1", "#64748b"]
LIMITS = dict(loss=(0, 25), jitter=(0, 60), delay=(1, 150), rate_pps=(100, 3000), queue=(10, 400), packets=(300, 6000))
DEFAULTS = dict(loss=2.0, jitter=10, delay=30, rate_pps=1000, queue=60, packets=2500,
                arms=["smart_true_fec", "smart_true", "smart", "aimd"])
COEX_DEFAULTS = dict(loss=0.1, jitter=3, delay=30, rate_pps=1000, queue=60, packets=3000, bg="cubic", k=2,
                     subjects=["smart", "smart_true"])

FLAGNAMES = [(0x01, "SYN"), (0x02, "ACK"), (0x04, "FIN"), (0x08, "DATA"), (0x10, "FEC")]


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def dissect(pl: bytes):
    """RUDP header: seq(4) ack(4) flags(1) crc(2) len(2) payload. CRC-16/CCITT-FALSE over seq,ack,flags,len,payload."""
    if len(pl) < 13: return None
    seq, ack, fl, crc, ln = struct.unpack(">IIBHH", pl[:13])
    body = pl[13:13 + ln]
    calc = crc16(pl[:9] + pl[11:13] + body)
    return dict(seq=seq, ack=ack, flags="|".join(n for b, n in FLAGNAMES if fl & b) or "-", fl=fl,
                crc="%04X" % crc, crc_ok=(calc == crc and len(body) == ln), len=ln)


class Arm:
    def __init__(self, idx, key, cfg, race_dir, role="subject", group=None, label=None, color=None):
        self.idx, self.key, self.cfg = idx, key, dict(cfg)
        self.role, self.group = role, group or key
        if label: self.cfg["label"] = label
        if color: self.cfg["color"] = color
        self.trace = os.path.join(race_dir, "%s.csv" % key)
        self.pcap = os.path.join(race_dir, "%s.pcap" % key)
        self.result_file = os.path.join(race_dir, "%s.out" % key)
        self.procs = []
        self.t_off = {"trace": 0, "pcap": 0}
        self.tbuf = b""
        self.pbuf = b""
        self.cwnd = 0.0; self.rtt = 0.0; self.ttrace = 0.0
        self.events = collections.deque()          # (t_ms, kind) for the loss-rate window
        self.max_ack = 0; self.base = None
        self.ack_hist = collections.deque()        # (t_ms, max_ack)
        self.goodput_mbps = 0.0
        self.fec_pkts = 0; self.data_pkts = 0
        self.pk_t0 = None
        self.pk_new = []                           # dissected packets since last push
        self.done = False; self.finish_ms = None; self.result = None
        self.srv_done_ts = None

    def poll(self):
        # ---- trace ----
        if os.path.exists(self.trace):
            with open(self.trace, "rb") as f:
                f.seek(self.t_off["trace"]); chunk = f.read(); self.t_off["trace"] += len(chunk)
            self.tbuf += chunk
            *lines, self.tbuf = self.tbuf.split(b"\n")
            for ln in lines:
                p = ln.decode(errors="ignore").split(",")
                if len(p) < 4 or p[0] == "time_ms": continue
                try: t = float(p[0]); self.cwnd = float(p[2]); self.rtt = float(p[3]); self.ttrace = t
                except ValueError: continue
                ev = p[1]
                self.events.append((t, "L" if ev in ("TIMEOUT", "FAST_RETX") else ("A" if ev == "ACK" else "O")))
        while self.events and self.events[0][0] < self.ttrace - 1000: self.events.popleft()
        # ---- pcap ----
        if os.path.exists(self.pcap):
            with open(self.pcap, "rb") as f:
                f.seek(self.t_off["pcap"]); chunk = f.read(); self.t_off["pcap"] += len(chunk)
            self.pbuf += chunk
            if self.t_off["pcap"] and len(self.pbuf) >= 24 and not getattr(self, "_hdr", False):
                self.pbuf = self.pbuf[24:]; self._hdr = True
            while getattr(self, "_hdr", False) and len(self.pbuf) >= 16:
                sec, us, incl, orig = struct.unpack("<IIII", self.pbuf[:16])
                if len(self.pbuf) < 16 + incl: break
                rec = self.pbuf[16:16 + incl]; self.pbuf = self.pbuf[16 + incl:]
                t = sec + us / 1e6
                if self.pk_t0 is None: self.pk_t0 = t
                trel = (t - self.pk_t0) * 1000.0
                to_server = rec[12:16] == bytes([192, 168, 10, 10])
                d = dissect(rec[28:])
                if not d: continue
                d["t"] = round(trel, 1); d["dir"] = "C>S" if to_server else "S>C"
                if to_server:
                    if d["fl"] & 0x10: self.fec_pkts += 1
                    elif d["fl"] & 0x08:
                        self.data_pkts += 1
                        if self.base is None or d["seq"] < self.base: self.base = d["seq"]
                else:
                    # ACK numbers are absolute sequence numbers (random initial value); count from the first data seq
                    if self.base is not None and d["fl"] & 0x02 and d["ack"] - self.base > self.max_ack and d["ack"] - self.base < 10**7:
                        self.max_ack = d["ack"] - self.base
                        self.ack_hist.append((trel, self.max_ack))
                if self.base is not None:
                    d["seq_r"] = d["seq"] - self.base if to_server else None
                    d["ack_r"] = d["ack"] - self.base if (not to_server and d["fl"] & 0x02) else None
                else:
                    d["seq_r"] = d["ack_r"] = None
                if len(self.pk_new) < 400: self.pk_new.append(d)
        while len(self.ack_hist) > 1 and self.ack_hist[-1][0] - self.ack_hist[0][0] > 500: self.ack_hist.popleft()
        if len(self.ack_hist) >= 2 and not self.done:
            (t0, a0), (t1, a1) = self.ack_hist[0], self.ack_hist[-1]
            if t1 > t0: self.goodput_mbps = (a1 - a0) * PAYLOAD * 8 / ((t1 - t0) * 1000.0)
        # sender exit -> RESULT
        c = self.procs[-1] if self.procs else None
        if c is not None and not self.done and c.poll() is not None:
            self.done = True
            try:
                txt = open(self.result_file).read()
                m = re.search(r"RESULT,(.*)", txt)
                if m:
                    f = m.group(1).split(",")
                    self.finish_ms = float(f[2]); self.result = dict(
                        packets=int(f[3]), sent=int(f[4]), retx=int(f[5]), timeouts=int(f[6]), fast=int(f[7]),
                        avg_rtt=float(f[8]), min_rtt=float(f[9]))
            except Exception: pass
            self.goodput_mbps = 0.0

    def loss_rate(self):
        e = list(self.events)
        a = sum(1 for _, k in e if k == "A"); l = sum(1 for _, k in e if k == "L")
        return l / (a + l) if a + l else 0.0

    def snapshot(self):
        pk, self.pk_new = self.pk_new[-40:], []
        return dict(policy=self.cfg["label"], role=self.role, group=self.group, color=self.cfg["color"], cwnd=round(self.cwnd, 2), rtt=round(self.rtt, 1),
                    loss=round(self.loss_rate(), 4), goodput=round(self.goodput_mbps, 3),
                    fec=dict(k=self.cfg["fec"], parity=self.fec_pkts, data=self.data_pkts,
                             mode="fixed XOR 1/%d" % self.cfg["fec"] if self.cfg["fec"] else "off"),
                    acked=self.max_ack, done=self.done, finish_ms=self.finish_ms, pk=pk)


def jain(x):
    x = [v for v in x if v is not None]
    q = sum(v * v for v in x)
    return (sum(x) ** 2) / (len(x) * q) if x and q > 0 else None


class Race:
    """mode 'race': every arm gets its own identical bottleneck.
       mode 'coexist': for every subject policy one bottleneck shared by the subject and k background flows (CUBIC or BBR-style);
       the group's result is frozen when its first flow finishes (the same rule as run_fairness_test.py)."""
    def __init__(self, params):
        self.id = uuid.uuid4().hex[:8]
        self.params = params
        self.mode = params.get("mode", "race")
        self.dir = tempfile.mkdtemp(prefix="studio_%s_" % self.id)
        self.t0 = time.time()
        self.arms = collections.OrderedDict()
        self.groups = collections.OrderedDict()      # gid -> dict(label, flows[], frozen)
        self.impairs = []
        self.state = "running"

    def plan(self):
        p = self.params; plan = []
        if self.mode == "race":
            for key in p["arms"]:
                plan.append((key, key, "subject", ARMS[key], None, None))
        else:
            for gi, subj in enumerate(p["subjects"]):
                gid = subj
                plan.append((subj, gid, "subject", ARMS[subj], None, None))
                for j in range(p["k"]):
                    plan.append(("%s:bg%d" % (gid, j + 1), gid, "bg", ARMS[p["bg"]], "%s #%d (vs %s)" % (ARMS[p["bg"]]["label"], j + 1, ARMS[subj]["label"].split(" (")[0]), BG_COLORS[j % 3]))
        return plan

    def start(self):
        p = self.params; plan = self.plan()
        self.data = os.path.join(self.dir, "payload.bin")
        with open(self.data, "wb") as fh: fh.write(os.urandom(p["packets"] * PAYLOAD))
        groups = collections.OrderedDict(); idx_in_group = collections.Counter()
        for (key, gid, role, cfg, label, color) in plan:
            a = Arm(len(self.arms), key, cfg, self.dir, role, gid, label, color); self.arms[key] = a
            i = idx_in_group[gid]; idx_in_group[gid] += 1
            a.sp, a.pp = SRV0 + i, PRX0 + i          # ports are reused group after group in coexist mode
            groups.setdefault(gid, []).append(a)
        for gid, flows in groups.items():
            self.groups[gid] = dict(label=ARMS[gid]["label"] if self.mode == "coexist" else flows[0].cfg["label"],
                                    flows=[f.key for f in flows], frozen=None, status="queued", impair=None)
        # race mode: every arm has its own bottleneck and all run at once. coexist mode: one policy group at a time, because
        # six or more busy-polling senders on two cores distort the very dynamics the test is about.
        self.queue = list(groups) if self.mode == "coexist" else None
        if self.mode == "coexist":
            self.launch(self.queue.pop(0))
        else:
            for gid in groups: self.launch(gid)
        self.t0 = time.time()

    def launch(self, gid):
        p = self.params; g = self.groups[gid]; flows = [self.arms[k] for k in g["flows"]]
        for a in flows:
            env = dict(os.environ, RUDP_QUIET="1", RUDP_OUT="/dev/null", RUDP_PORT=str(a.sp))
            a.procs.append(subprocess.Popen([os.path.join(BIN, "server")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        g["impair"] = subprocess.Popen([os.path.join(BIN, "impair")] + ["%d:%d" % (f.pp, f.sp) for f in flows] + [
            "--delay", str(p["delay"]), "--jitter", str(p["jitter"]), "--loss", str(p["loss"]), "--rate", str(p["rate_pps"]),
            "--queue", str(p["queue"]), "--seed", str(p.get("seed", 1))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.impairs.append(g["impair"])
        time.sleep(0.4)
        for a in flows:
            env = dict(os.environ, RUDP_LIVE="1")
            if a.cfg["fec"]: env["RUDP_FEC"] = str(a.cfg["fec"])
            out = open(a.result_file, "w")
            cmd = [os.path.join(BIN, "smart_client")] + a.cfg["flags"].split() + [
                "--emu", "0", "--port", str(a.pp), "--file", self.data, "--seed", str(p.get("seed", 1) * 10 + a.idx),
                "--trace", a.trace, "--pcap", a.pcap]
            a.procs.append(subprocess.Popen(cmd, env=env, stdout=out, stderr=subprocess.STDOUT))
        g["status"] = "running"; g["t0"] = time.time()

    def poll(self):
        for a in self.arms.values(): a.poll()
        if self.mode == "coexist" and self.state == "running":
            for gid, g in self.groups.items():
                if g["status"] != "running": continue
                if g["frozen"] is None and any(self.arms[k].done for k in g["flows"]):
                    acked = [self.arms[k].max_ack for k in g["flows"]]
                    n = len(acked); tot = float(sum(acked)) or 1.0; fair = tot / n
                    g["frozen"] = dict(acked=acked, J=jain(acked), subject_rel=acked[0] / fair, min_rel=min(acked) / fair)
                    for k in g["flows"]:
                        a = self.arms[k]
                        if not a.done:
                            try: a.procs[-1].terminate()
                            except Exception: pass
                            a.done = True; a.goodput_mbps = 0.0
                if all(self.arms[k].done for k in g["flows"]):          # group finished: free its ports, start the next policy
                    g["status"] = "done"
                    for k in g["flows"]:
                        for pr in self.arms[k].procs[:-1]:
                            try: pr.terminate(); pr.wait(timeout=1.0)
                            except Exception:
                                try: pr.kill()
                                except Exception: pass
                    try: g["impair"].terminate(); g["impair"].wait(timeout=1.0)
                    except Exception:
                        try: g["impair"].kill()
                        except Exception: pass
                    if self.queue: self.launch(self.queue.pop(0))
        if self.state == "running" and all(a.done for a in self.arms.values()) and not getattr(self, "queue", None):
            self.state = "finished"
            self.cleanup(servers_only=True)

    def cleanup(self, servers_only=False):
        victims = [pr for a in self.arms.values() for pr in (a.procs[:-1] if servers_only else a.procs)] + list(self.impairs)
        for pr in victims:
            try: pr.terminate()
            except Exception: pass
        for pr in victims:                      # make sure the ports are free before the next race can start
            try: pr.wait(timeout=1.0)
            except Exception:
                try: pr.kill(); pr.wait(timeout=1.0)
                except Exception: pass

    def stop(self):
        self.cleanup()
        self.state = "stopped" if self.state == "running" else self.state

    def group_view(self):
        out = {}
        for gid, g in self.groups.items():
            fl = [self.arms[k] for k in g["flows"]]
            live = jain([a.goodput_mbps for a in fl]) if any(a.goodput_mbps > 0 for a in fl) else None
            acked = [a.max_ack for a in fl]; tot = sum(acked) or 1
            out[gid] = dict(label=g["label"], flows=g["flows"], live_jain=live, shares=[v / tot for v in acked], frozen=g["frozen"], status=g["status"])
        return out

    def summary(self):
        res = {}
        for k, a in self.arms.items():
            res[k] = dict(label=a.cfg["label"], role=a.role, group=a.group, finish_ms=a.finish_ms, **(a.result or {}))
        fin = [(v["finish_ms"], k) for k, v in res.items() if v.get("finish_ms") and v["role"] == "subject"]
        win = min(fin)[1] if fin and self.mode == "race" else None
        return dict(id=self.id, mode=self.mode, state=self.state, params=self.params, results=res, winner=win,
                    groups=self.group_view() if self.mode == "coexist" else None, elapsed=round(time.time() - self.t0, 2))


app = FastAPI(title="Poise-RUDP Studio")
CUR = {"race": None}
HISTORY = collections.OrderedDict()


def ensure_built():
    os.makedirs(BIN, exist_ok=True)
    cf = "-O2 -std=c11 -D_POSIX_C_SOURCE=200809L -D_GNU_SOURCE -w"
    jobs = [("smart_client", "src/smart_client.c src/rudp_sender.c src/common.c -lm"),
            ("server", "src/server.c src/common.c"), ("impair", "src/impair.c -lm")]
    for name, srcs in jobs:
        out = os.path.join(BIN, name)
        deps = [os.path.join(ROOT, s) for s in srcs.split() if s.endswith(".c")]
        if os.path.exists(out) and all(os.path.getmtime(out) >= os.path.getmtime(d) for d in deps): continue
        r = subprocess.run("gcc %s -o %s %s" % (cf, out, srcs), shell=True, cwd=ROOT, capture_output=True, text=True)
        if r.returncode: sys.exit("build failed: " + r.stderr)


@app.get("/", response_class=HTMLResponse)
def index():
    return open(os.path.join(HERE, "templates", "index.html"), encoding="utf-8").read()


@app.get("/api/config")
def config():
    return dict(arms={k: dict(label=v["label"], color=v["color"], fec=v["fec"]) for k, v in ARMS.items()},
                limits=LIMITS, defaults=DEFAULTS, coexist_defaults=COEX_DEFAULTS,
                true_smart_ready=os.path.exists(SMART_TRUE))


def clamp(v, lo, hi, name):
    try: v = float(v)
    except Exception: raise HTTPException(400, "bad " + name)
    return min(hi, max(lo, v))


@app.post("/api/race")
async def start_race(body: dict):
    r = CUR["race"]
    if r and r.state == "running": raise HTTPException(409, "a race is already running")
    p = {k: clamp(body.get(k, DEFAULTS[k]), *LIMITS[k], k) for k in LIMITS}
    p["packets"] = int(p["packets"]); p["queue"] = int(p["queue"])
    p["seed"] = int(body.get("seed", int(time.time()) % 1000)); p["mode"] = "race"
    arms = [a for a in body.get("arms", DEFAULTS["arms"]) if a in ARMS][:4]
    if not arms: raise HTTPException(400, "no valid arms")
    p["arms"] = list(dict.fromkeys(arms))
    race = Race(p); race.start()
    CUR["race"] = race; HISTORY[race.id] = race
    while len(HISTORY) > 20: HISTORY.popitem(last=False)
    return dict(id=race.id)


@app.post("/api/coexist")
async def start_coexist(body: dict):
    """Coexistence arena: each subject policy shares ONE bottleneck with k CUBIC / BBR-style flows; Jain's index is reported live."""
    r = CUR["race"]
    if r and r.state == "running": raise HTTPException(409, "a race is already running")
    p = {k: clamp(body.get(k, COEX_DEFAULTS[k]), *LIMITS[k], k) for k in LIMITS}
    p["packets"] = int(p["packets"]); p["queue"] = int(p["queue"])
    p["bg"] = body.get("bg", "cubic"); p["k"] = int(clamp(body.get("k", 2), 1, 3, "k"))
    if p["bg"] not in ("cubic", "bbr"): raise HTTPException(400, "bg must be cubic or bbr")
    subj = [a for a in body.get("subjects", COEX_DEFAULTS["subjects"]) if a in ARMS and a not in ("cubic", "bbr", "aimd")][:2]
    if not subj: raise HTTPException(400, "no valid subjects")
    p["subjects"] = list(dict.fromkeys(subj)); p["mode"] = "coexist"; p["seed"] = int(body.get("seed", int(time.time()) % 1000))
    race = Race(p); race.start()
    CUR["race"] = race; HISTORY[race.id] = race
    while len(HISTORY) > 20: HISTORY.popitem(last=False)
    return dict(id=race.id)


@app.get("/api/race/{rid}")
def get_race(rid: str):
    r = HISTORY.get(rid)
    if not r: raise HTTPException(404)
    r.poll()
    return r.summary()


@app.post("/api/stop")
def stop():
    r = CUR["race"]
    if r: r.stop()
    return dict(ok=True)


@app.get("/api/fairness")
def fairness():
    p = os.path.join(ROOT, "results", "fairness", "summary.csv")
    if not os.path.exists(p): return dict(rows=[], note="run run_fairness_test.py first")
    rows = [l for l in open(p).read().splitlines() if l and not l.startswith("#")]
    head = rows[0].split(",")
    out = []
    for l in rows[1:]:
        c = l.split(",")
        if len(c) == len(head) and c[0] != "": out.append(dict(zip(head, c)))
    return dict(rows=out)


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    final_sent = None
    try:
        while True:
            r = CUR["race"]
            if r is None:
                await sock.send_text(json.dumps(dict(type="idle", state="none"))); await asyncio.sleep(1.0); continue
            r.poll()
            if r.state == "running":
                final_sent = None
                await sock.send_text(json.dumps(dict(type="telemetry", race=r.id, state=r.state, t=round((time.time() - r.t0) * 1000),
                                                     arms={k: a.snapshot() for k, a in r.arms.items()}, mode=r.mode, groups=r.group_view() if r.mode == "coexist" else None)))
            elif final_sent != r.id:
                final_sent = r.id
                await sock.send_text(json.dumps(dict(type="telemetry", race=r.id, state=r.state, t=round((time.time() - r.t0) * 1000),
                                                     arms={k: a.snapshot() for k, a in r.arms.items()}, mode=r.mode, groups=r.group_view() if r.mode == "coexist" else None)))
                await sock.send_text(json.dumps(dict(type="summary", **r.summary())))
            else:
                await asyncio.sleep(0.9)
            await asyncio.sleep(0.1)
    except (WebSocketDisconnect, RuntimeError):
        return


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8000); ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    ensure_built()
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
