#!/usr/bin/env python3
"""Baselines on the same simulated paths as the stress test.
 - plain UDP : send everything once, no ACKs, no retransmission (reports delivered fraction)
 - TCP model : cwnd with slow start, congestion avoidance, fast retransmit/recovery with selective ACKs,
               retransmission timer per RFC 6298 (min 200 ms, exponential backoff), initial window 10, cap 32.
               This is a MODEL of TCP, not the kernel stack. Real kernel TCP needs tc/netem: see scripts/netem_baselines.sh.
 python3 web/baselines.py"""
import heapq, os, random, statistics as st, sys
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cc import Controller, load_q
from dqn import DQN
from sim import run
from stress import sample_path, DEEP

CAP = 32

class Path:
    def __init__(s, rng, loss, delay, jitter, rate, queue):
        s.rng, s.loss, s.delay, s.jitter, s.rate, s.queue, s.free = rng, loss, delay, jitter, rate, queue, 0.0
    def data_arrival(s, t):
        if s.rng.random() < s.loss: return None
        at = t
        if s.rate:
            start = max(t, s.free)
            if (start - t) * s.rate > s.queue: return None
            s.free = start + 1.0 / s.rate; at = s.free
        return at + s.delay + s.rng.uniform(0, s.jitter)
    def ack_arrival(s, t):
        return None if s.rng.random() < s.loss else t + s.delay + s.rng.uniform(0, s.jitter)

def plain_udp(p, seed):
    rng = random.Random(seed); path = Path(rng, p["loss"], p["delay"], p["jitter"], p["rate"], p["queue"])
    got, last = 0, 0.0
    for _ in range(p["n"]):
        a = path.data_arrival(0.0)
        if a is not None: got += 1; last = max(last, a)
    return got / p["n"], last

def tcp_model(p, seed, max_t=30.0):
    rng = random.Random(seed); path = Path(rng, p["loss"], p["delay"], p["jitter"], p["rate"], p["queue"])
    n = p["n"]; ev, cnt = [], 0
    def push(t, k, d=None):
        nonlocal cnt; cnt += 1; heapq.heappush(ev, (t, cnt, k, d))
    cwnd, ssthresh = 10.0, 1e9
    una = nxt = 0; sacked = set(); rcvd = set(); expected = 0
    sent_t, retx = {}, set(); rtxq = []; dups = 0; recover = -1; in_rec = False
    srtt = rttvar = None; rto = 1.0; rto_at = None
    t = 0.0
    inflight = set()
    def pipe(): return len(inflight)
    def send(seq, is_retx):
        nonlocal rto_at
        sent_t[seq] = t; inflight.add(seq)
        if is_retx: retx.add(seq)
        a = path.data_arrival(t)
        if a is not None: push(a, "data", seq)
        if rto_at is None: rto_at = t + rto
    def pump():
        while pipe() < min(int(cwnd), CAP):
            while rtxq and (rtxq[0] < una or rtxq[0] in sacked): rtxq.pop(0)
            if rtxq: send(rtxq.pop(0), True)
            elif nxt < n:
                nonlocal_nxt_inc(); 
            else: break
    def nonlocal_nxt_inc():
        nonlocal nxt
        send(nxt, False); nxt += 1
    pump()
    push(0.0, "tick")
    while ev:
        t, _, k, d = heapq.heappop(ev)
        if t > max_t: return max_t
        if k == "data":
            rcvd.add(d)
            while expected in rcvd: expected += 1
            a = path.ack_arrival(t)
            if a is not None: push(a, "ack", (expected, frozenset(x for x in rcvd if x >= expected)))
        elif k == "ack":
            ack, sk = d; sacked |= set(sk); inflight -= set(sk)
            if ack > una:
                if sent_t.get(ack - 1) is not None and (ack - 1) not in retx:
                    r = t - sent_t[ack - 1]
                    if srtt is None: srtt, rttvar = r, r / 2
                    else: rttvar = 0.75 * rttvar + 0.25 * abs(srtt - r); srtt = 0.875 * srtt + 0.125 * r
                    rto = max(0.2, srtt + 4 * rttvar)
                acked = ack - una; una = ack; dups = 0
                sacked = {x for x in sacked if x >= una}
                inflight -= {x for x in inflight if x < una}
                if in_rec and una >= recover: in_rec = False; cwnd = ssthresh
                elif not in_rec:
                    cwnd += acked if cwnd < ssthresh else acked / cwnd
                rto_at = t + rto if una < nxt else None
                if una >= n: return t
            else:
                dups += 1
                if dups >= 3 and not in_rec:
                    ssthresh = max(2.0, (nxt - una) / 2); cwnd = ssthresh; in_rec = True; recover = nxt
                    rtxq.insert(0, una); inflight.discard(una)
                if in_rec and sacked:                       # selective retransmit of holes below the highest SACK
                    hi = max(sacked)
                    for q in range(una, hi):
                        if q not in sacked and q not in rtxq and q not in retx: rtxq.append(q); inflight.discard(q)
            pump()
        elif k == "tick":
            if rto_at is not None and t >= rto_at and una < nxt:
                ssthresh = max(2.0, (nxt - una) / 2); cwnd = 1.0; in_rec = False; dups = 0
                rto = min(8.0, rto * 2); retx.discard(una)
                rtxq = [q for q in range(una, nxt) if q not in sacked]; retx.clear(); inflight.clear(); rto_at = t + rto
            pump(); push(t + 0.01, "tick")
    return max_t

def one(args):
    qpath, p = args
    q = load_q(qpath); net = DQN(); net.load(DEEP) if os.path.exists(DEEP) else None
    out = {}
    udp = [plain_udp(p, 900 + sd) for sd in range(3)]
    out["udp_delivered"] = st.mean(u[0] for u in udp)
    out["tcp"] = st.mean(tcp_model(p, 900 + sd) for sd in range(3))
    for m in ("fixed", "aimd", "smart", "deep"):
        ts = []
        for sd in range(3):
            c = Controller(m, [r[:] for r in q] if m == "smart" else None, net=net if m == "deep" else None); c.learn = False
            ts.append(run(c, p["loss"], p["delay"], n_msgs=p["n"], rate=p["rate"], queue=p["queue"], seed=900 + sd, jitter=p["jitter"]))
        out[m] = st.mean(ts)
    return p, out

if __name__ == "__main__":
    qpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results", "q_chat.txt")
    rng = random.Random(424242); paths = [sample_path(rng) for _ in range(48)]
    with Pool(os.cpu_count()) as pool: res = pool.map(one, [(qpath, p) for p in paths])
    print("48 random paths x 3 seeds (same paths as stress.py)")
    print(f"  plain UDP: mean delivered {100*st.mean(o['udp_delivered'] for _, o in res):.1f}%; complete delivery on {sum(1 for _, o in res if o['udp_delivered'] == 1.0)} of 48 paths")
    for m in ("tcp", "fixed", "aimd", "smart", "deep"):
        v = [o[m] for _, o in res]; print(f"  mean time {m:6s}: {st.mean(v):6.2f} s  median {st.median(v):5.2f} s")
    for a in ("smart", "deep"):
        w = sum(1 for _, o in res if o[a] < o["tcp"] * 0.95); l = sum(1 for _, o in res if o[a] > o["tcp"] * 1.05)
        print(f"  {a:5s} vs TCP model: faster on {w}, slower on {l}, within 5% on {48-w-l}")
    w = sum(1 for _, o in res if o["aimd"] < o["tcp"] * 0.95); l = sum(1 for _, o in res if o["aimd"] > o["tcp"] * 1.05)
    print(f"  AIMD  vs TCP model: faster on {w}, slower on {l}, within 5% on {48-w-l}")
