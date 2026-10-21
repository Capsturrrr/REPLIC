"""
Window controllers shared by the web chat gateway and the offline trainer/simulator.
Modes: fixed (8), aimd (rule), smart (tabular Q-learning, same 80 states / 5 actions as src/rl_cc.h).
All time is passed in, so the same code runs in real time and in a simulator.
"""
import math, os, random

RL_MULT = [0.5, 0.85, 1.0, 1.15, 1.5]
CWND_MAX = 32.0   # feature normalisation (matches the C agent)
SS_DEEP = 0.0    # >0: the neural agent starts with classic slow start up to this window (the gateway sets 64, like smart_client)
CAP = 32.0        # window ceiling; the gateway raises it to 128 like smart_client
INIT_CWND = 10.0          # RFC 6928 initial window; used by BOTH aimd and smart for a fair comparison
FIXED_WINDOW = 8

def rl_state(rtt_ratio, loss, cwnd):
    rb = 0 if rtt_ratio < 1.15 else 1 if rtt_ratio < 1.5 else 2 if rtt_ratio < 2.2 else 3
    lb = 0 if loss <= 0 else 1 if loss < 0.03 else 2 if loss < 0.10 else 3
    cb = 0 if cwnd < 4 else 1 if cwnd < 12 else 2 if cwnd < 32 else 3 if cwnd < 64 else 4
    return (rb * 4 + lb) * 5 + cb

def prior_q():
    q = []
    for s in range(80):
        cb_loss, cb_rtt = (s // 5) % 4, (s // 5) // 4
        d = 1.0 if (cb_rtt <= 1 and cb_loss <= 1) else -1.0 if (cb_rtt >= 2 or cb_loss >= 2) else 0.0
        q.append([0.25 * d * math.log(m) / math.log(1.5) for m in RL_MULT])
    return q

def load_q(path):
    try:
        rows = [list(map(float, l.split())) for l in open(path).read().strip().splitlines()[1:]]
        if len(rows) == 80 and all(len(r) == 5 for r in rows): return rows
    except Exception: pass
    return None

def save_q(q, path):
    with open(path, "w") as f:
        f.write("0\n")
        for r in q: f.write(" ".join(f"{v:.6f}" for v in r) + " \n")

LAG = 1
MASK = True
DELAY_W = 0.5          # weight of the RTT-inflation penalty in the reward
RANDOM_LOSS_RATIO = 0.0   # >0: loss with RTT inflation below this ratio is treated as non-congestive (no shrink)
HYBRID = float(os.environ.get("REPLIC_HYBRID", "0"))     # deep mode: hand control to AIMD while the per-interval loss fraction is >= this (0 = off, 0.10 recommended)
TIMEOUT_CUT = float(os.environ.get("REPLIC_TCUT", "0"))        # deep/smart modes: on a retransmission timeout multiply the window by this (0 = off). Safety net borrowed from AIMD.
FEAT_ZERO = ()         # feature indices blanked out (ablation studies only)

def deep_feat(ratio, loss, cwnd, thr, last_a):
    """Continuous state for the neural agent (the table agent buckets the first three)."""
    f = [min(ratio - 1.0, 3.0) / 3.0, min(loss, 0.3) / 0.3, cwnd / CWND_MAX, thr, RL_MULT[last_a] - 1.0]
    for i in FEAT_ZERO: f[i] = 0.0
    return f

class Controller:
    def __init__(self, mode="fixed", q=None, alpha=0.15, gamma=0.9, eps=0.0, rng=None, net=None):
        self.net = net
        self.net_learn = net is not None and eps > 0   # frozen unless trained with exploration
        self.prev_a = 2
        self.q, self.alpha, self.gamma, self.eps = q, alpha, gamma, eps
        self.rng = rng or random.Random()
        self.learn = True
        self.reset(mode)

    def reset(self, mode=None):
        if mode: self.mode = mode
        self.cwnd = float(FIXED_WINDOW) if self.mode == "fixed" else INIT_CWND
        self.ssthresh = CAP if CAP > 32 else 16.0
        self.ss = self.mode == "deep" and SS_DEEP > 0
        if self.ss: self.ssthresh = SS_DEEP
        self.srtt = self.min_rtt = None
        self.iv_start = None
        self.iv_new = self.iv_lossev = self.iv_acked = 0
        self.iv_rtts = []
        self.last_sa = None
        self.hist = []
        self.prev = None
        self.prev_a = 2
        self.guard = 0
        self.last_action = "-"
        self.note = None

    def set_mode(self, m):
        if m == self.mode: return
        self.reset(m)

    def window(self):
        return FIXED_WINDOW if self.mode == "fixed" else max(1, int(self.cwnd))

    # ---- events from the transport ----
    def on_new_packet(self): self.iv_new += 1

    def on_ack(self, n_acked, rtt_samples):
        self.iv_acked += n_acked
        for r in rtt_samples:
            self.iv_rtts.append(r)
            self.srtt = r if self.srtt is None else 0.875 * self.srtt + 0.125 * r
            self.min_rtt = r if self.min_rtt is None else min(self.min_rtt, r)
        if self.mode == "aimd" or self.guard > 0 or self.ss:
            for _ in range(n_acked):
                self.cwnd = min(CAP, self.cwnd + (1.0 if self.cwnd < self.ssthresh else 1.0 / self.cwnd))
            if self.ss and self.cwnd >= SS_DEEP: self.ss = False

    def on_loss(self, kind):
        self.iv_lossev += 1
        self.ss = False
        if self.guard > 0:
            self.ssthresh = max(2.0, self.cwnd / 2)
            self.cwnd = 1.0 if kind == "timeout" else self.ssthresh
            return
        if kind == "timeout" and TIMEOUT_CUT > 0 and self.mode in ("deep", "smart"):
            self.cwnd = max(2.0, self.cwnd * TIMEOUT_CUT)
        if self.mode == "aimd":
            self.ssthresh = max(2.0, self.cwnd / 2)
            self.cwnd = 1.0 if kind == "timeout" else self.ssthresh

    # ---- smart controller: decide about once per RTT ----
    def step(self, now, backlog):
        if self.iv_start is None: self.iv_start = now
        interval = max(0.1, min(0.5, self.srtt or 0.5))
        if now - self.iv_start < interval: return
        dur = now - self.iv_start
        new, lossev, acked, rtts = self.iv_new, self.iv_lossev, self.iv_acked, self.iv_rtts
        self.iv_start, self.iv_new, self.iv_lossev, self.iv_acked, self.iv_rtts = now, 0, 0, 0, []
        deep = self.mode == "deep" and self.net is not None
        if not deep and (self.mode != "smart" or self.q is None): return
        if (new == 0 and acked == 0 and lossev == 0): return
        base = self.min_rtt or 0.05
        cur = (sum(rtts) / len(rtts)) if rtts else (self.srtt or base)
        ratio = max(1.0, cur / base)
        loss = min(1.0, lossev / max(1, new))
        peak = max(1.0, CWND_MAX * dur / max(base, 0.02))
        thr = min(1.0, acked / peak)
        if self.ss and deep:                       # slow start: the policy is not consulted until the first congestion signal
            if loss > 0 or ratio >= 1.3: self.ss = False
            else: self.last_action = "slow start"; self.note = f"deep: slow start, cwnd {self.cwnd:.1f}"; return
        if deep and HYBRID > 0:
            if loss >= HYBRID: self.guard = 3
            elif self.guard > 0: self.guard -= 1
            if self.guard > 0:
                self.last_action = "AIMD"; self.note = f"deep: loss {loss*100:.0f}% -> AIMD in control, cwnd {self.cwnd:.1f}"; return
        allowed = range(5)
        if MASK and loss == 0 and ratio < 1.5:
            allowed = (2, 3, 4)          # no congestion signal: never shrink the window
        elif MASK and RANDOM_LOSS_RATIO > 0 and ratio < RANDOM_LOSS_RATIO:
            allowed = (2, 3, 4)          # loss without queueing delay: looks random, not congestion
        r = thr - DELAY_W * max(0.0, ratio - 1.0) - 4.0 * loss
        if deep:
            f = deep_feat(ratio, loss, self.cwnd, thr, self.prev_a)
            if self.learn and self.net_learn and self.prev is not None:
                self.net.store(self.prev[0], self.prev[1], r, f); self.net.train_step()
            if self.eps > 0 and self.rng.random() < self.eps: a = self.rng.choice(list(allowed))
            else:
                qv = self.net.q(f); a = max(allowed, key=lambda k: qv[k])
            self.prev = (f, a); self.prev_a = a
        else:
            st = rl_state(ratio, loss, self.cwnd)
            if self.learn and len(self.hist) >= LAG:
                ps, pa = self.hist[-LAG]
                self.q[ps][pa] += self.alpha * (r + self.gamma * max(self.q[st]) - self.q[ps][pa])
            if self.eps > 0 and self.rng.random() < self.eps: a = self.rng.choice(list(allowed))
            else: a = max(allowed, key=lambda k: self.q[st][k])
            self.last_sa = (st, a)
            self.hist = (self.hist + [(st, a)])[-2:]
        self.cwnd = max(2.0, min(CAP, self.cwnd * RL_MULT[a]))
        self.last_action = f"x{RL_MULT[a]}"
        self.note = f"{self.mode}: rtt x{ratio:.1f}, loss {loss*100:.0f}%, cwnd -> {self.cwnd:.1f} ({self.last_action})"
