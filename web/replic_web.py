#!/usr/bin/env python3
"""
REPLIC Web Chat: a browser front end for the REPLIC chat.
Browsers cannot send raw UDP, so this small gateway speaks REPLIC (same 13-byte
header, checksum, Go-Back-N, cumulative ACKs, fast retransmit as src/chat.c)
to the peer over UDP and serves a chat page over HTTP on localhost.
It interoperates with ./bin/chat on the other side.

  python3 web/replic_web.py --udp 9001 --peer 100.88.28.25:9001 --name Harsh --http 8080
  then open http://localhost:8080
Standard library only.
"""
import argparse, collections, heapq, json, os, select, socket, struct, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc import Controller, load_q
from dqn import DQN
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SYN, ACK, FIN, DATA = 1, 2, 4, 8
WINDOW, TIMEOUT_MS, DUP = 8, 300, 3
SRW = 256   # Selective Repeat window and ACK bitmap size (32 bytes), identical to the C transport


def checksum(seq, ack, flags, payload):
    """CRC-16/CCITT-FALSE over seq, ack, flags, payload_len, payload (same as src/common.c)."""
    crc = 0xFFFF
    for b in struct.pack(">IIBH", seq, ack, flags, len(payload)) + bytes(payload):
        crc ^= b << 8
        for _ in range(8): crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def pack(seq, ack, flags, payload=b""):
    return struct.pack(">IIBHH", seq, ack, flags, checksum(seq, ack, flags, payload), len(payload)) + payload

def unpack(b):
    if len(b) < 13: return None
    seq, ack, flags, chk, n = struct.unpack(">IIBHH", b[:13])
    p = b[13:13 + n]
    if len(p) != n or chk != checksum(seq, ack, flags, p): return None
    return seq, ack, flags, p


class Chat:
    def __init__(s, udp, peer, name, loss, delay=0):
        s.name, s.loss, s.lock = name, loss, threading.Lock()
        s.delay, s.dq, s.dseq = delay / 1000.0, [], 0
        s.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sock.bind(("0.0.0.0", udp))
        ip, port = peer.rsplit(":", 1)
        s.peer = (ip, int(port)); s.heard = False
        s.sendq, s.sent_at = [], []
        s.base = s.next = s.expected = s.dups = 0
        s.sack_samples, s.sack_done = [], set()
        s.adaptive_rto, s.rto, s.rs, s.rv = False, TIMEOUT_MS / 1000.0, None, 0.0   # RFC 6298 state (seconds)
        s.gbn = False                        # True: original Go-Back-N behaviour (set with --gbn)
        s.sacked, s.rbuf = set(), {}         # Selective Repeat: packets the peer reported / out-of-order packets we hold
        s.recover = -1                       # no new fast retransmit until this seq is acked (stops dup-ACK storms)
        s.msgs, s.log = [], []
        s.stats = dict(sent=0, dropped=0, retx=0)
        s.import_random = __import__("random")
        here = os.path.dirname(os.path.abspath(__file__))
        q = load_q(os.path.join(here, "..", "results", "q_chat.txt")) or load_q(os.path.join(here, "..", "results", "q_lossy.txt"))
        net = None
        try:
            dp = os.path.join(here, "..", "results", "dqn_chat.json")
            if os.path.exists(dp): net = DQN(); net.load(dp)
        except Exception: net = None
        s.cc = Controller("fixed", q, net=net)
        s.deep_ok = net is not None
        s.agent_ok = q is not None
        s.first_sent, s.retxed = {}, set()
        s.pending = collections.deque()
        s.bulk = None                        # dict(total, start, done_at, mode)
        s.hist, s.last_samp, s.t0 = collections.deque(maxlen=300), 0.0, time.time()
        s.last_drop, s.last_sent = 0, 0

    def note(s, text, kind="info"):
        s.log.append(dict(t=time.strftime("%H:%M:%S"), kind=kind, text=text))
        s.log = s.log[-200:]

    def window(s):
        return s.cc.window()

    def set_mode(s, m):
        if m not in ("fixed", "aimd", "smart", "deep"): return
        if m == "deep" and not s.deep_ok: return
        s.cc.set_mode(m)
        s.note(f"window controller: {m}", "ok")

    def tx(s, flags, seq, ack, payload=b"", retx=False):
        if flags & DATA and not retx: s.cc.on_new_packet()
        s.stats["sent"] += 1
        if retx: s.stats["retx"] += 1
        if s.loss > 0 and s.import_random.random() * 100 < s.loss:
            s.stats["dropped"] += 1
            if flags & DATA:
                s.note(f"network lost message #{seq}" + (" (retransmission)" if retx else ""), "loss")
            return
        raw = pack(seq, ack, flags, payload)
        if s.delay > 0:
            s.dseq += 1
            heapq.heappush(s.dq, (time.time() + s.delay, s.dseq, raw, s.peer))
        else:
            s.sock.sendto(raw, s.peer)

    def resend(s, frm, retx=True):
        for q in range(frm, s.next):
            s.retxed.add(q)
            s.sent_at[q] = time.time()
            s.tx(DATA | ACK, q, s.expected, s.sendq[q], retx)

    def resend_holes(s):
        """Selective Repeat: resend only the unreported packets below the highest one the peer reported (or just the base)."""
        hi = (max(s.sacked) if s.sacked else s.base) + 1
        for q in range(s.base, min(hi, s.next)):
            if q not in s.sacked:
                s.retxed.add(q); s.sent_at[q] = time.time(); s.tx(DATA | ACK, q, s.expected, s.sendq[q], True)

    def send(s, text):
        with s.lock:
            s.pending.append(f"{s.name}: {text}"[:400].encode())
            s.pump()
            return True

    def bulk_start(s, n=60):
        with s.lock:
            s.bulk = dict(total=n, start=time.time(), done_at=None, mode=s.cc.mode, first=s.next + len(s.pending))
            for i in range(n):
                s.pending.append(f"{s.name}: test message {i + 1}/{n}".encode())
            s.note(f"stress test: {n} messages, controller = {s.cc.mode}", "ok")
            s.pump()

    def pump(s):
        while s.pending and s.next - s.base < s.window():
            payload = s.pending.popleft()
            s.sendq.append(payload); s.sent_at.append(time.time())
            s.first_sent[s.next] = time.time()
            s.msgs.append(dict(seq=s.next, mine=True, text=payload.decode(errors="replace"), t=time.strftime("%H:%M")))
            s.tx(DATA | ACK, s.next, s.expected, payload)
            s.next += 1

    def on_acked(s, old_base, new_base):
        now = time.time()
        s.sacked = {q for q in s.sacked if q >= new_base}
        samples, s.sack_samples = s.sack_samples, []
        for q in range(old_base, new_base):
            t0 = s.first_sent.pop(q, None)
            if t0 is not None and q not in s.retxed and q not in s.sack_done:       # Karn: skip retransmitted samples; SACKed ones were sampled already
                samples.append(now - t0)
            s.retxed.discard(q); s.sack_done.discard(q)
        s.cc.on_ack(new_base - old_base, samples)
        for r in samples:                                   # RFC 6298 RTO estimator (used only with --rto adaptive)
            if s.rs is None: s.rs, s.rv = r, r / 2
            else: s.rv = 0.75 * s.rv + 0.25 * abs(s.rs - r); s.rs = 0.875 * s.rs + 0.125 * r
            if s.adaptive_rto: s.rto = min(3.0, max(0.2, s.rs + 4 * s.rv))
        if s.bulk and s.bulk["done_at"] is None and new_base >= s.bulk["first"] + s.bulk["total"]:
            s.bulk["done_at"] = time.time()
            s.note(f"stress test done: {s.bulk['total']} messages in {s.bulk['done_at'] - s.bulk['start']:.1f} s ({s.bulk['mode']})", "ok")

    def loss_event(s, kind):
        s.cc.on_loss(kind)

    def rl_step(s):
        s.cc.step(time.time(), len(s.pending))
        if s.cc.note:
            s.note(s.cc.note, "info"); s.cc.note = None

    def handle(s, raw, src):
        pk = unpack(raw)
        if pk is None:
            s.note("dropped corrupted packet", "loss"); return
        seq, ack, flags, p = pk
        if src != s.peer: s.peer = src
        if not s.heard:
            s.heard = True; s.note(f"connected to {src[0]}:{src[1]}", "ok")
        if flags & ACK:
            if not s.gbn and not (flags & DATA) and len(p) == SRW // 8:       # bitmap: bit j%8 of byte j//8 set = peer holds packet ack+j
                for j in range(1, SRW):
                    q = ack + j
                    if (p[j // 8] >> (j % 8)) & 1 and s.base <= q < s.next:
                        if q not in s.sacked and q not in s.retxed and q in s.first_sent:
                            s.sack_samples.append(time.time() - s.first_sent[q]); s.sack_done.add(q)   # RTT is taken when the packet is first reported, not when a hole before it is filled
                        s.sacked.add(q)
            if s.base < ack <= s.next:
                s.on_acked(s.base, ack)
                s.base, s.dups = ack, 0
            elif not (flags & DATA) and ack == s.base and s.base < s.next:
                s.dups += 1
                if s.dups >= DUP and s.base > s.recover:
                    s.note(f"3 duplicate ACKs: fast retransmit of the missing packets from #{s.base}" if not s.gbn else f"3 duplicate ACKs: fast retransmit from #{s.base}", "retx")
                    s.loss_event("dup"); (s.resend(s.base) if s.gbn else s.resend_holes()); s.recover = s.next - 1; s.dups = 0
        if flags & DATA:
            if seq == s.expected:
                s.msgs.append(dict(seq=seq, mine=False, text=p.decode(errors="replace"), t=time.strftime("%H:%M")))
                s.expected += 1
                while s.expected in s.rbuf:                    # buffered packets that are now in order
                    s.msgs.append(dict(seq=s.expected, mine=False, text=s.rbuf.pop(s.expected).decode(errors="replace"), t=time.strftime("%H:%M")))
                    s.expected += 1
            elif seq < s.expected:
                s.note(f"duplicate #{seq} discarded", "info")
            elif not s.gbn and seq < s.expected + SRW:
                if seq not in s.rbuf: s.rbuf[seq] = p
                s.note(f"out of order: buffered #{seq}, expected #{s.expected}", "info")
            else:
                s.note(f"out of order: got #{seq}, expected #{s.expected}, discarded", "info")
            if s.gbn: s.tx(ACK, 0, s.expected)
            else:
                bm = bytearray(SRW // 8)
                for q in s.rbuf:
                    j = q - s.expected
                    if 0 < j < SRW: bm[j // 8] |= 1 << (j % 8)
                s.tx(ACK, 0, s.expected, bytes(bm))

    def loop(s):
        last_punch = 0
        while True:
            with s.lock:
                if not s.heard and time.time() - last_punch > 1:
                    last_punch = time.time(); s.tx(ACK, 0, s.expected)
                if s.gbn and s.base < s.next and (time.time() - s.sent_at[s.base]) * 1000 > s.rto * 1000:
                    s.note(f"timeout: resending #{s.base}..#{s.next - 1}", "retx")
                    s.loss_event("timeout"); s.resend(s.base); s.recover = s.next - 1
                    if s.adaptive_rto: s.rto = min(3.0, s.rto * 2)      # exponential backoff
                if not s.gbn and s.base < s.next:                # Selective Repeat: every packet has its own timer
                    now_ = time.time(); rto_ = s.rto
                    exp = [q for q in range(s.base, s.next) if q not in s.sacked and now_ - s.sent_at[q] > rto_]
                    if exp:
                        if exp[0] > s.recover:                   # penalise once per loss event
                            s.note(f"timeout: resending only the missing packets (#{exp[0]}...)", "retx")
                            s.loss_event("timeout"); s.recover = s.next - 1
                            if s.adaptive_rto: s.rto = min(3.0, s.rto * 2)
                        for q in exp:
                            s.retxed.add(q); s.sent_at[q] = now_; s.tx(DATA | ACK, q, s.expected, s.sendq[q], True)
                s.rl_step(); s.pump(); s.sample()
            with s.lock:
                while s.dq and s.dq[0][0] <= time.time():
                    _, _, raw_, dst_ = heapq.heappop(s.dq)
                    s.sock.sendto(raw_, dst_)
            r, _, _ = select.select([s.sock], [], [], 0.01)
            if r:
                raw, src = s.sock.recvfrom(2048)
                with s.lock: s.handle(raw, src)

    def sample(s):
        """Record one point of the live dashboard (about 5 per second, last 60 s kept)."""
        now = time.time()
        if now - s.last_samp < 0.2: return
        s.last_samp = now
        d, sn = s.stats["dropped"] - s.last_drop, s.stats["sent"] - s.last_sent
        s.last_drop, s.last_sent = s.stats["dropped"], s.stats["sent"]
        s.hist.append(dict(t=round(now - s.t0, 1), cwnd=round(s.cc.cwnd, 1), win=s.window(), fl=s.next - s.base,
                           rtt=round((s.cc.srtt or 0) * 1000), base=round((s.cc.min_rtt or 0) * 1000),
                           loss=round(100.0 * d / sn, 1) if sn else 0.0, mode=s.cc.mode, act=s.cc.last_action))

    def snapshot(s):
        with s.lock:
            ms = [dict(m, delivered=(m["mine"] and m["seq"] < s.base)) for m in s.msgs]
            return dict(name=s.name, peer=f"{s.peer[0]}:{s.peer[1]}", connected=s.heard, loss=s.loss,
                        base=s.base, next=s.next, expected=s.expected, window=s.window(), mode=s.cc.mode, cwnd=round(s.cc.cwnd, 1), queued=len(s.pending), agent_ok=s.agent_ok, deep_ok=s.deep_ok, last_action=s.cc.last_action, bulk=(dict(s.bulk, elapsed=round((s.bulk['done_at'] or time.time()) - s.bulk['start'], 1), done=s.bulk['done_at'] is not None) if s.bulk else None),
                        stats=dict(s.stats), hist=list(s.hist), rto=round(s.rto * 1000), rto_mode="adaptive" if s.adaptive_rto else "fixed", srtt=round((s.cc.srtt or 0) * 1000), msgs=ms, log=s.log[-60:])


def make_handler(chat, html):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if urlparse(self.path).path == "/state": self._send(200, json.dumps(chat.snapshot()))
            else: self._send(200, html, "text/html; charset=utf-8")
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or b"{}")
            path = urlparse(self.path).path
            if path == "/send":
                ok = chat.send(str(d.get("text", ""))[:370]) if str(d.get("text", "")).strip() else False
                self._send(200, json.dumps(dict(ok=ok)))
            elif path == "/mode":
                with chat.lock: chat.set_mode(str(d.get("mode")))
                self._send(200, "{}")
            elif path == "/bulk":
                chat.bulk_start(int(d.get("n", 60))); self._send(200, "{}")
            elif path == "/loss":
                chat.loss = max(0, min(90, int(d.get("loss", 0)))); self._send(200, "{}")
            else: self._send(404, "{}")
    return H

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp", type=int, default=9001); ap.add_argument("--peer", required=True)
    ap.add_argument("--name", default="me"); ap.add_argument("--http", type=int, default=8080)
    ap.add_argument("--loss", type=int, default=0); ap.add_argument("--delay", type=int, default=0, help="emulated one-way delay in ms")
    ap.add_argument("--rto", choices=["fixed", "adaptive"], default="adaptive", help="retransmission timeout: RFC 6298 adaptive (default) or fixed 300 ms")
    ap.add_argument("--tcut", type=float, default=0.0, help="agent safety net: multiply the window by this on a retransmission timeout (0 = off, 0.25 recommended)")
    ap.add_argument("--gbn", action="store_true", help="use the original Go-Back-N behaviour instead of Selective Repeat")
    ap.add_argument("--cap", type=float, default=128.0, help="window ceiling (packets); 128 matches smart_client, 32 restores the original")
    ap.add_argument("--ssdeep", type=float, default=64.0, help="neural agent: classic slow start up to this window before the policy takes over (0 = off)")
    ap.add_argument("--hybrid", type=float, default=0.10, help="neural agent hands control to AIMD while the loss fraction per interval is >= this (0 = off, 0.10 recommended)")
    a = ap.parse_args()
    import cc as _cc; _cc.TIMEOUT_CUT = a.tcut; _cc.HYBRID = a.hybrid; _cc.CAP = min(a.cap, 250.0); _cc.SS_DEEP = a.ssdeep
    import os
    html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8").read()
    chat = Chat(a.udp, a.peer, a.name, a.loss, a.delay)
    chat.adaptive_rto = a.rto == "adaptive"; chat.gbn = a.gbn
    threading.Thread(target=chat.loop, daemon=True).start()
    print(f"REPLIC Web Chat: open http://localhost:{a.http}   (UDP {a.udp} -> {a.peer})")
    ThreadingHTTPServer(("0.0.0.0", a.http), make_handler(chat, html)).serve_forever()
