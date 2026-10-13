"""
Discrete-event simulator of the chat transport (Go-Back-N, cumulative ACKs, 3-dup-ACK fast retransmit,
300 ms timeout) over a path with one-way delay, random loss, and an optional bottleneck queue.
Used to train and evaluate the window controllers on many paths quickly.
"""
import heapq, os, random
INORDER_DEFAULT = os.environ.get("POISE_INORDER") == "1"   # POISE_INORDER=1: jitter delays packets but never reorders them (like the C emulated path without --reorder)
ARTO_DEFAULT = os.environ.get("POISE_ARTO") == "1"       # POISE_ARTO=1: RFC 6298 adaptive retransmission timeout (as in the C smart client) instead of fixed 300 ms
SR_DEFAULT = os.environ.get("POISE_SR") == "1"       # POISE_SR=1: Selective Repeat (selective acknowledgments) instead of Go-Back-N in every run() call
RG_DEFAULT = os.environ.get("POISE_RG") == "1"   # POISE_RG=1: realistic fast-retransmit (no re-trigger within one recovery), as in the C smart client
from cc import Controller

TIMEOUT = 0.3
TICK = 0.01

def run(ctrl, loss, delay, n_msgs=60, rate=None, queue=20, seed=0, max_t=30.0, jitter=0.0, sack=False, recover_guard=False):
    recover_guard = recover_guard or RG_DEFAULT
    sack = sack or SR_DEFAULT
    inorder = INORDER_DEFAULT
    rs, rv, rto = None, 0.0, TIMEOUT
    last_arr = {"data": 0.0, "ack": 0.0}
    def arrive(kind, at):
        if inorder:
            at = max(at, last_arr[kind]); last_arr[kind] = at
        return at
    rng = random.Random(seed)
    ev, cnt = [], 0
    def push(t, kind, data=None):
        nonlocal cnt
        cnt += 1; heapq.heappush(ev, (t, cnt, kind, data))
    t = 0.0
    base = nxt = 0
    sent_at, first_sent, retxed = {}, {}, set()
    expected = 0
    rcvd, sacked = set(), set()
    dups = 0
    recover = -1         # with recover_guard: no new fast retransmit until data outstanding at the last one is acked
    link_free = 0.0      # bottleneck serialization for data direction
    ctrl.reset()
    pending = n_msgs

    def tx_data(seq, retx):
        nonlocal link_free
        if not retx: ctrl.on_new_packet()
        if rng.random() < loss: return
        at = t
        if rate:
            start = max(t, link_free)
            if (start - t) * rate > queue: return       # drop-tail queue full
            link_free = start + 1.0 / rate
            at = link_free
        push(arrive("data", at + delay + rng.uniform(0, jitter)), "data", seq)

    def tx_ack(ackn):
        if rng.random() < loss: return
        push(arrive("ack", t + delay + rng.uniform(0, jitter)), "ack", (ackn, frozenset(rcvd)) if sack else ackn)

    def pump():
        nonlocal nxt, pending
        while pending and nxt - base < ctrl.window():
            first_sent[nxt] = t; sent_at[nxt] = t
            tx_data(nxt, False); nxt += 1; pending -= 1

    def resend(dupack=False):
        hi = (max(sacked) if sacked else base) if (sack and dupack) else nxt
        for q in range(base, max(hi, base + 1) if (sack and dupack) else nxt):
            if sack and q in sacked: continue
            retxed.add(q); sent_at[q] = t; tx_data(q, True)

    push(0.0, "tick")
    pump()
    while ev:
        t, _, kind, data = heapq.heappop(ev)
        if t > max_t: return max_t
        if kind == "data":
            if sack:
                rcvd.add(data)
                while expected in rcvd: expected += 1
                rcvd.intersection_update({x for x in rcvd if x >= expected})
            elif data == expected: expected += 1
            tx_ack(expected)
        elif kind == "ack":
            if sack: data, sk = data; sacked.update(sk)
            if base < data <= nxt:
                samples = []
                for q in range(base, data):
                    t0 = first_sent.pop(q, None)
                    if t0 is not None and q not in retxed: samples.append(t - t0)
                    retxed.discard(q)
                ctrl.on_ack(data - base, samples)
                if ARTO_DEFAULT:
                    for r_ in samples:
                        if rs is None: rs, rv = r_, r_ / 2
                        else: rv = 0.75 * rv + 0.25 * abs(rs - r_); rs = 0.875 * rs + 0.125 * r_
                        rto = min(3.0, max(0.2, rs + 4 * rv))
                base, dups = data, 0
                sacked.difference_update({x for x in sacked if x < base})
                if base >= n_msgs: return t
            elif data == base and base < nxt:
                dups += 1
                if dups >= 3 and (not recover_guard or base > recover):
                    ctrl.on_loss("dup"); resend(True); dups = 0; recover = nxt - 1
        elif kind == "tick":
            if base < nxt and t - sent_at[base] > rto:
                ctrl.on_loss("timeout"); resend(); recover = nxt - 1
                if ARTO_DEFAULT: rto = min(3.0, rto * 2)
            ctrl.step(t, pending)
            pump()
            push(t + TICK, "tick")
    return max_t
