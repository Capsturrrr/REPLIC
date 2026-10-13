#!/usr/bin/env python3
"""
train_smart_poise.py - train the Poise congestion controller with PPO and export it to C.

What is trained
  * policy  pi(a | s):  tanh MLP 8 -> 32 -> 32 -> 1 (mean of a Gaussian over a in (-1,1) via tanh); window factor C = 2**a in [0.5, 2.0]
  * critic  V(s, z):    tanh MLP (8+5) -> 64 -> 64 -> 1 (training only, not exported); z = scenario descriptors (flow count, link rate,
                        RTT, buffer/BDP, loss) that only the critic sees (asymmetric actor-critic)
  * state s_t (8 values, defined once in src/smart_cc.h and computed by that same C code in training and deployment):
      SRTT gradient, queue-delay trend, queue-delay level, loss ratio, in-flight/BDP, competing-traffic pressure
      (ACK-arrival burstiness), previous action, log2(cwnd).

Reward (native multi-objective, per control interval, per learned flow i)
      R = w1*Throughput - w2*DelayPenalty - w3*LossPenalty - w4*FairnessPenalty
      Throughput      = packets acked by flow i / (mu * T)                        (fraction of the link, 0..1)
      DelayPenalty    = min(qd, 2),   qd = (avgRTT - minRTT) / minRTT            (queueing delay relative to propagation)
      LossPenalty     = min(loss events / new packets, 0.3)
      FairnessPenalty = g * [ (1 - J) + 0.5*max(0, s_i - 1) ]
          J   = Jain's index over the interval rates of all active flows, J = (sum x)^2 / (n sum x^2)
          s_i = x_i / mean(x)   (> 1 means flow i takes more than its equal share; weighted 0.5)
          g   = clip((sum x / mu - 0.8) / 0.15, 0, 1)   fairness only counts when the link is actually contended
      The fairness term uses simulator-only global information (other flows' rates). That is allowed in training
      (centralised training); at run time the policy sees only its own local state.

Training environment: sim/netsim.c (packet-level dumbbell, ctypes). Each episode randomises link rate, RTT, buffer, random loss,
jitter, mid-episode capacity changes, 0-3 competing flows drawn from {CUBIC, BBR-style, AIMD, copies of the learned policy}
and late-joining flows.

Usage
  python3 train_smart_poise.py --iters 300 --w4 2.0 --tag fair          # train + export (writes smart_fair.txt, results/smart/)
  python3 train_smart_poise.py --iters 300 --w4 0   --tag nofair        # ablation: same everything, no fairness term
  python3 train_smart_poise.py --export smart_fair.npz                  # write neural_weights.h for compile-time use
  python3 train_smart_poise.py --eval smart_fair.npz                    # simulator evaluation vs CUBIC / BBR / AIMD
No PyTorch needed: PPO with hand-written backprop in numpy (the network is tiny).
"""
import argparse, ctypes, hashlib, json, math, os, subprocess, sys, time
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
SM_IN, SM_H = 8, 32
PRIV = 5      # extra critic-only inputs (scenario descriptors); the policy never sees them
CWND_MAX = 128.0


# ------------------------------------------------------------------ simulator binding
def load_sim():
    so = os.path.join(ROOT, "sim", "libnetsim.so")
    src = os.path.join(ROOT, "sim", "netsim.c")
    if not os.path.exists(so) or os.path.getmtime(so) < max(os.path.getmtime(src), os.path.getmtime(os.path.join(ROOT, "src", "smart_cc.h"))):
        r = subprocess.run(["gcc", "-O2", "-shared", "-fPIC", "-w", "-o", so, src, "-lm"], capture_output=True, text=True)
        if r.returncode: sys.exit("netsim build failed:\n" + r.stderr)
    L = ctypes.CDLL(so)
    d = ctypes.c_double; p = ctypes.c_void_p; i = ctypes.c_int
    L.sim_new.restype = p; L.sim_new.argtypes = [d, d, i, d, d, ctypes.c_uint]
    L.sim_free.argtypes = [p]; L.sim_set_mu.argtypes = [p, d]
    L.sim_add_flow.argtypes = [p, i, d, d, d, d, d]; L.sim_add_flow.restype = i
    L.sim_set_cwnd.argtypes = [p, i, d]; L.sim_set_ss.argtypes = [p, i, i]; L.sim_set_action.argtypes = [p, i, d]
    L.sim_state.argtypes = [p, i, ctypes.POINTER(d)]; L.sim_run.argtypes = [p, d]
    L.sim_features.argtypes = [p, i, ctypes.POINTER(d), ctypes.POINTER(d)]; L.sim_features.restype = i
    L.sim_ivstat.argtypes = [p, i, ctypes.POINTER(d)]
    return L


LIB = load_sim()
T_AIMD, T_CUBIC, T_BBR, T_LEARNED = 1, 2, 3, 0
KIND = {"learned": 0, "aimd": 1, "cubic": 2, "bbr": 3}


# ------------------------------------------------------------------ networks (numpy, manual backprop)
class MLP:
    def __init__(self, nin, nh, nout, rng, out_scale=1.0):
        self.p = {"W1": rng.normal(0, 1 / math.sqrt(nin), (nh, nin)), "b1": np.zeros(nh),
                  "W2": rng.normal(0, 1 / math.sqrt(nh), (nh, nh)), "b2": np.zeros(nh),
                  "W3": rng.normal(0, out_scale / math.sqrt(nh), (nout, nh)), "b3": np.zeros(nout)}

    def fwd(self, X):
        p = self.p
        h1 = np.tanh(X @ p["W1"].T + p["b1"]); h2 = np.tanh(h1 @ p["W2"].T + p["b2"])
        return h2 @ p["W3"].T + p["b3"], (X, h1, h2)

    def bwd(self, cache, dout):
        X, h1, h2 = cache; p = self.p
        g = {"W3": dout.T @ h2, "b3": dout.sum(0)}
        dz2 = (dout @ p["W3"]) * (1 - h2 ** 2)
        g["W2"] = dz2.T @ h1; g["b2"] = dz2.sum(0)
        dz1 = (dz2 @ p["W2"]) * (1 - h1 ** 2)
        g["W1"] = dz1.T @ X; g["b1"] = dz1.sum(0)
        return g


class Adam:
    def __init__(self, params, lr):
        self.lr = lr; self.t = 0; self.m = {k: np.zeros_like(v) for k, v in params.items()}; self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, params, grads, clip=1.0):
        n = math.sqrt(sum(float((g ** 2).sum()) for g in grads.values()))
        sc = min(1.0, clip / (n + 1e-8)); self.t += 1
        for k in params:
            g = grads[k] * sc
            self.m[k] = 0.9 * self.m[k] + 0.1 * g; self.v[k] = 0.999 * self.v[k] + 0.001 * g * g
            params[k] -= self.lr * (self.m[k] / (1 - 0.9 ** self.t)) / (np.sqrt(self.v[k] / (1 - 0.999 ** self.t)) + 1e-8)


class Agent:
    def __init__(self, seed=0, log_std=-0.9):
        r = np.random.default_rng(seed)
        self.actor = MLP(SM_IN, SM_H, 1, r, out_scale=0.05)
        self.critic = MLP(SM_IN + PRIV, 64, 1, r, out_scale=1.0)
        self.log_std = np.array([log_std])

    def mean(self, X):
        return np.tanh(self.actor.fwd(X)[0][:, 0])

    def act(self, x, rng, explore=True):
        m = float(self.mean(x[None, :])[0]); s = float(np.exp(self.log_std[0]))
        a = m + s * rng.normal() if explore else m
        logp = -0.5 * ((a - m) / s) ** 2 - self.log_std[0] - 0.5 * math.log(2 * math.pi)
        return a, logp

    def value(self, x, priv=None):
        z = np.concatenate([x, priv if priv is not None else np.zeros(PRIV)])
        return float(self.critic.fwd(z[None, :])[0][0, 0])


# ------------------------------------------------------------------ reward
W = dict(w1=1.0, w2=0.5, w3=3.0, w4=1.5)
BBR_MASS = 0.25
BBR_RANDOM = False   # --bbr-random: randomise the simulated BBR competitor's pacing/cwnd gains
OVERSHARE = 0.5      # extra one-sided weight on taking more than the equal share (Jain's index alone is symmetric)


def jain(x):
    x = np.asarray(x, float); q = (x ** 2).sum()
    return float(x.sum() ** 2 / (len(x) * q)) if q > 0 and len(x) > 0 else 1.0


def reward(i, rates, mu, T_ms, qd, lossf, active, w):
    """R = w1*Throughput - w2*DelayPenalty - w3*LossPenalty - w4*FairnessPenalty  (see module docstring)."""
    cap = mu * T_ms / 1000.0
    thr = min(1.0, rates[i] * T_ms / 1000.0 / cap) if cap > 0 else 0.0
    delay = min(qd, 2.0)
    loss = min(lossf, 0.3)
    fair = 0.0
    xs = np.array([rates[j] for j in active])
    if len(xs) >= 2 and w["w4"] != 0:
        g = min(1.0, max(0.0, (xs.sum() / mu - 0.8) / 0.15))
        s_i = rates[i] / (xs.mean() + 1e-9)
        fair = g * ((1.0 - jain(xs)) + OVERSHARE * max(0.0, s_i - 1.0))
    return w["w1"] * thr - w["w2"] * delay - w["w3"] * loss - w["w4"] * fair, dict(thr=thr, delay=delay, loss=loss, fair=fair)


# ------------------------------------------------------------------ environment
def sample_cfg(rng, force=None):
    mu = float(10 ** rng.uniform(math.log10(300), math.log10(2000)))
    rtt = float(rng.uniform(20, 150))
    bdp = mu * rtt / 1000.0
    cfg = dict(mu=mu, rtt=rtt, buf=int(max(15, rng.uniform(0.5, 3.0) * bdp)),
               loss=float(rng.choice([0.0, 0.1, rng.uniform(0.1, 3.0)])), jitter=float(rng.uniform(0, 0.1) * rtt),
               dur=float(rng.uniform(14, 26)) * 1000, mu_change=None, seed=int(rng.integers(1, 1 << 30)))
    n_bg = int(rng.choice([0, 1, 2, 3], p=[0.15, 0.35, 0.30, 0.20]))
    pb = BBR_MASS; cfg["bg"] = [str(rng.choice(["cubic", "bbr", "aimd", "self"], p=[0.6 - pb, pb, 0.15, 0.25])) for _ in range(n_bg)]
    cfg["start"] = [0.0] + [float(rng.choice([0.0, rng.uniform(0, 3000)])) for _ in range(n_bg)]
    if rng.random() < 0.3: cfg["start"][0] = float(rng.uniform(1000, 4000)) if n_bg else 0.0   # agent joins an occupied link
    if rng.random() < 0.25: cfg["mu_change"] = (float(rng.uniform(0.3, 0.8)) * cfg["dur"], float(rng.uniform(0.4, 1.6)))
    if force: cfg.update(force)
    return cfg


def run_episode(agent, cfg, rng, w, explore=True, record=True):
    """One episode. Every learned flow (the agent, plus 'self' copies) is controlled by `agent`. Returns trajectories and metrics."""
    sim = LIB.sim_new(cfg["mu"], cfg["rtt"], cfg["buf"], cfg["loss"], cfg["jitter"], cfg["seed"])
    kinds = ["learned"] + [("learned" if b == "self" else b) for b in cfg["bg"]]
    for k, kind in enumerate(kinds):
        if kind == "bbr" and BBR_RANDOM:       # real BBR-style senders are more aggressive than the textbook model: randomise gains
            LIB.sim_add_flow(sim, KIND[kind], 10.0, CWND_MAX, cfg["start"][k], float(rng.uniform(1.0, 1.3)), float(rng.choice([2.0, 2.0, 3.0, 4.0])))
        else:
            LIB.sim_add_flow(sim, KIND[kind], 10.0, CWND_MAX, cfg["start"][k], 128.0, 64.0)
    n = len(kinds); learned = [k for k, kd in enumerate(kinds) if kd == "learned"]
    x = (ctypes.c_double * SM_IN)(); aux = (ctypes.c_double * 8)(); st = (ctypes.c_double * 8)(); iv = (ctypes.c_double * 4)()
    traj = {k: dict(x=[], a=[], lp=[], r=[], v=[], pv=[]) for k in learned}
    priv_base = [n / 4.0, math.log10(cfg['mu']) - 2.5, cfg['rtt'] / 150.0, cfg['buf'] / max(1.0, cfg['mu'] * cfg['rtt'] / 1000.0) / 3.0, cfg['loss'] / 3.0]
    pend = {}; mu = cfg["mu"]; t = 0.0; T = 100.0
    acc = dict(thr=0.0, delay=0.0, loss=0.0, fair=0.0, n=0)
    while t < cfg["dur"]:
        LIB.sim_run(sim, T); t += T
        if cfg["mu_change"] and t >= cfg["mu_change"][0]:
            mu = cfg["mu"] * cfg["mu_change"][1]; LIB.sim_set_mu(sim, mu); cfg["mu_change"] = None
        rates = {}; obs = {}
        for k in range(n):
            if k in learned:
                act = LIB.sim_features(sim, k, x, aux)
                obs[k] = (np.array(list(x)), list(aux), act); rates[k] = aux[4] / (aux[5] / 1000.0) if aux[5] > 0 else 0.0
            else:
                LIB.sim_ivstat(sim, k, iv); rates[k] = iv[0] / (iv[2] / 1000.0) if iv[2] > 0 else 0.0
        active = [k for k in range(n) if rates[k] > 0]
        for k in learned:
            xk, ax, act = obs[k]
            if not act: continue
            if k in pend:                                    # reward for the action taken at the start of this interval
                r, parts = reward(k, rates, mu, ax[5], ax[0], ax[2], active if k in active else active + [k], w)
                px, pa, plp, pv, ppv = pend.pop(k)
                if record: tr = traj[k]; tr["x"].append(px); tr["a"].append(pa); tr["lp"].append(plp); tr["v"].append(pv); tr["pv"].append(ppv); tr["r"].append(r)
                if k == 0:
                    for q in parts: acc[q] += parts[q]
                    acc["n"] += 1
            LIB.sim_state(sim, k, st)
            if st[5] > 0.5:                                  # classic slow start until the first congestion signal
                if ax[1] / max(st[3], 1.0) >= 1.3 or ax[2] > 0: LIB.sim_set_ss(sim, k, 0)   # same rule as the sender: avg RTT / min RTT >= 1.3 or any loss
                continue
            a, lp = agent.act(xk, rng, explore); a_env = max(-1.0, min(1.0, a))
            LIB.sim_set_cwnd(sim, k, st[0] * 2.0 ** a_env); LIB.sim_set_action(sim, k, a_env)
            pv_ = np.array(priv_base); pend[k] = (xk, a, lp, agent.value(xk, pv_) if record else 0.0, pv_)
        LIB.sim_state(sim, 0, st)
        T = min(500.0, max(100.0, st[2])) if st[2] > 0 else 100.0
    last_v = {k: (agent.value(pend[k][0], pend[k][4]) if k in pend and record else 0.0) for k in learned}
    final = []
    for k in range(n):
        LIB.sim_state(sim, k, st); final.append(st[4])
    LIB.sim_free(sim)
    return traj, last_v, dict(acc=acc, final=final, kinds=kinds)


def gae(tr, last_v, gamma=0.95, lam=0.95):
    r = np.array(tr["r"]); v = np.array(tr["v"] + [last_v]); adv = np.zeros(len(r)); g = 0.0
    for t in reversed(range(len(r))):
        d = r[t] + gamma * v[t + 1] - v[t]; g = d + gamma * lam * g; adv[t] = g
    return adv, adv + v[:-1]


# ------------------------------------------------------------------ PPO
def ppo_update(agent, opt_a, opt_c, X, XC, A, LP, ADV, RET, clip=0.2, epochs=6, mb=512, ent=0.003, rng=None):
    N = len(X); ADV = (ADV - ADV.mean()) / (ADV.std() + 1e-8); stats = []
    for ep in range(epochs):
        idx = rng.permutation(N); kl_stop = False
        for s in range(0, N, mb):
            b = idx[s:s + mb]; xb, xcb, ab, lpb, advb, retb = X[b], XC[b], A[b], LP[b], ADV[b], RET[b]; m = len(b)
            z, cache = agent.actor.fwd(xb); mean = np.tanh(z[:, 0]); sd = float(np.exp(agent.log_std[0]))
            lp = -0.5 * ((ab - mean) / sd) ** 2 - agent.log_std[0] - 0.5 * math.log(2 * math.pi)
            ratio = np.exp(lp - lpb)
            active = ((advb > 0) & (ratio < 1 + clip)) | ((advb < 0) & (ratio > 1 - clip))
            coef = np.where(active, -advb * ratio, 0.0) / m
            dmean = coef * (ab - mean) / sd ** 2
            ga = agent.actor.bwd(cache, (dmean * (1 - mean ** 2) + 1e-3 * z[:, 0] / m)[:, None])   # + tiny L2 on the pre-tanh output
            dls = np.array([float((coef * ((ab - mean) ** 2 / sd ** 2 - 1)).sum()) - ent])
            opt_a.step({**agent.actor.p, "ls": agent.log_std}, {**ga, "ls": dls})
            agent.log_std[0] = float(np.clip(agent.log_std[0], -2.5, 0.0))
            v, cc = agent.critic.fwd(xcb)
            gc = agent.critic.bwd(cc, 0.5 * (v[:, 0] - retb)[:, None] / m * 2.0)
            opt_c.step(agent.critic.p, gc)
            kl = float(np.mean(lpb - lp)); stats.append(kl)
            if kl > 0.04: kl_stop = True; break
        if kl_stop: break
    return float(np.mean(stats))


def train(args):
    rng = np.random.default_rng(args.seed)
    global OVERSHARE, BBR_RANDOM, BBR_MASS
    BBR_RANDOM = args.bbr_random; BBR_MASS = args.bbr_weight
    w = dict(W); w["w4"] = args.w4
    if args.w1 is not None: w["w1"] = args.w1
    if args.w2 is not None: w["w2"] = args.w2
    if args.w3 is not None: w["w3"] = args.w3
    if args.overshare is not None: OVERSHARE = args.overshare
    best = -1e9
    agent = Agent(args.seed)
    opt_a = Adam({**agent.actor.p, "ls": agent.log_std}, args.lr); opt_c = Adam(agent.critic.p, args.lr * 2)
    os.makedirs(os.path.join(ROOT, "results", "smart"), exist_ok=True)
    log = open(os.path.join(ROOT, "results", "smart", "train_%s.csv" % args.tag), "w")
    log.write("iter,episodes,steps,mean_reward,thr,delay,loss,fair,log_std,kl,seconds\n")
    t0 = time.time()
    for it in range(1, args.iters + 1):
        Xs, XCs, As, LPs, ADVs, RETs = [], [], [], [], [], []; acc = dict(thr=0, delay=0, loss=0, fair=0, n=0); rsum = 0.0; steps = 0
        for e in range(args.episodes):
            cfg = sample_cfg(rng)
            traj, last_v, info = run_episode(agent, cfg, rng, w)
            for k, tr in traj.items():
                if len(tr["r"]) < 2: continue
                adv, ret = gae(tr, last_v[k])
                Xs.append(np.array(tr["x"])); XCs.append(np.hstack([np.array(tr["x"]), np.array(tr["pv"])])); As.append(np.array(tr["a"])); LPs.append(np.array(tr["lp"])); ADVs.append(adv); RETs.append(ret)
                rsum += sum(tr["r"]); steps += len(tr["r"])
            for q in acc: acc[q] += info["acc"][q]
        if not Xs: continue
        kl = ppo_update(agent, opt_a, opt_c, np.vstack(Xs), np.vstack(XCs), np.concatenate(As), np.concatenate(LPs), np.concatenate(ADVs),
                        np.concatenate(RETs), rng=rng)
        nn = max(1, acc["n"])
        line = "%d,%d,%d,%.4f,%.3f,%.3f,%.4f,%.3f,%.3f,%.4f,%.0f" % (it, args.episodes, steps, rsum / max(1, steps), acc["thr"] / nn, acc["delay"] / nn,
                                                                     acc["loss"] / nn, acc["fair"] / nn, agent.log_std[0], kl, time.time() - t0)
        log.write(line + "\n"); log.flush()
        if it % 5 == 0 or it == 1: print("it %3d  steps %5d  R %.3f  thr %.2f delay %.2f loss %.3f fair %.3f  std %.2f  kl %.4f  %.0fs" %
                                          (it, steps, rsum / max(1, steps), acc["thr"] / nn, acc["delay"] / nn, acc["loss"] / nn, acc["fair"] / nn,
                                           float(np.exp(agent.log_std[0])), kl, time.time() - t0), flush=True)
        if it % 20 == 0 or it == args.iters:
            save(agent, args, w, it)
            sc = validate(agent)       # model selection on held-out simulator seeds (900+); the real-proxy tests use different seeds and a real network path
            print("   validation score %.3f (J %.3f, util %.2f, subj %.2f)" % (sc[0], sc[1], sc[2], sc[3]), flush=True)
            if sc[0] > best: best = sc[0]; save(agent, args, w, it, suffix="_best")
    return agent


# ------------------------------------------------------------------ export
def save(agent, args, w, it, suffix=""):
    p = agent.actor.p
    np.savez(os.path.join(ROOT, "results", "smart", "smart_%s%s.npz" % (args.tag, suffix)), **{k: v.astype(np.float32) for k, v in p.items()},
             iters=it, w=json.dumps(dict(w, overshare=OVERSHARE)))
    write_txt(p, os.path.join(ROOT, "results", "smart", "smart_%s%s.txt" % (args.tag, suffix)))


class _Det:
    """deterministic view of a live agent for validation"""
    def __init__(self, ag): self.ag = ag
    def act(self, x, rng, explore=False): return self.ag.act(x, rng, False)


def validate(agent, reps=3):
    pol = _Det(agent); rs = [eval_pair("policy", bg, k, 900 + r, pol, dur=24000.0, bbr_aggr=(BBR_RANDOM and bg == "bbr" and r % 2 == 1)) for bg in ("cubic", "bbr") for k in (1, 2, 3) for r in range(reps)]
    J = float(np.mean([r["J"] for r in rs])); U = float(np.mean([min(1.0, r["util"]) for r in rs])); S = float(np.mean([r["subj"] for r in rs]))
    alone = [eval_pair("policy", "cubic", 0, 950 + r, pol, dur=20000.0) for r in range(2)]
    ua = float(np.mean([min(1.0, r["util"]) for r in alone]))
    return J + 0.25 * U + 0.25 * ua - 0.15 * float(np.mean([abs(r["subj"] - 1.0) for r in rs])), J, U, S


def write_txt(p, path):
    with open(path, "w") as f:
        f.write("SMART1 %d %d\n" % (SM_IN, SM_H))
        for k in ("W1", "b1", "W2", "b2", "W3", "b3"):
            f.write(" ".join("%.9g" % v for v in np.asarray(p[k], np.float32).ravel()) + "\n")


def export_header(npz, out):
    z = np.load(npz); meta = json.loads(str(z["w"])) if "w" in z else {}
    p = {k: z[k] for k in ("W1", "b1", "W2", "b2", "W3", "b3")}
    h = hashlib.sha256(b"".join(p[k].tobytes() for k in sorted(p))).hexdigest()[:16]

    def arr(name, a, dims):
        flat = ", ".join("%.9gf" % v for v in np.asarray(a, np.float32).ravel())
        return "static const float %s%s = {%s};\n" % (name, dims, flat)
    with open(out, "w") as f:
        f.write("/* neural_weights.h - generated by train_smart_poise.py --export. DO NOT EDIT.\n"
                " * policy: tanh MLP %d -> %d -> %d -> 1, action a = tanh(output) in (-1, 1), cwnd factor C = 2^a\n"
                " * trained iterations: %s   reward weights: %s   sha256[:16]: %s */\n" % (SM_IN, SM_H, SM_H, int(z["iters"]) if "iters" in z else "?", json.dumps(meta), h))
        f.write("#ifndef NEURAL_WEIGHTS_H\n#define NEURAL_WEIGHTS_H\n")
        f.write(arr("SM_W1", p["W1"], "[%d*%d]" % (SM_H, SM_IN)))
        f.write(arr("SM_B1", p["b1"], "[%d]" % SM_H)); f.write(arr("SM_W2", p["W2"], "[%d*%d]" % (SM_H, SM_H)))
        f.write(arr("SM_B2", p["b2"], "[%d]" % SM_H)); f.write(arr("SM_W3", p["W3"], "[%d]" % SM_H))
        f.write("static const float SM_B3 = %.9gf;\n#endif\n" % float(p["b3"][0]))
    print("wrote", out, "sha", h)


# ------------------------------------------------------------------ simulator evaluation
class FixedPolicy:
    def __init__(self, npz):
        z = np.load(npz); self.p = {k: z[k].astype(np.float64) for k in ("W1", "b1", "W2", "b2", "W3", "b3")}

    def act(self, x, rng, explore=False):
        h1 = np.tanh(self.p["W1"] @ x + self.p["b1"]); h2 = np.tanh(self.p["W2"] @ h1 + self.p["b2"])
        return float(np.tanh((self.p["W3"] @ h2 + self.p["b3"])[0])), 0.0

    def value(self, x): return 0.0


def eval_pair(subject, bg, k, seed, policy=None, dur=30000.0, bbr_aggr=False):
    """subject in {'policy','aimd','cubic','bbr'} vs k greedy `bg` flows on the 1000 pps / 44 ms / 60 pkt bottleneck of the proxy tests."""
    cfg = dict(mu=1000.0, rtt=44.0, buf=60, loss=0.1, jitter=3.0, dur=dur, mu_change=None, seed=seed, bg=[bg] * k, start=[0.0] * (k + 1))
    rng = np.random.default_rng(seed)
    sim = LIB.sim_new(cfg["mu"], cfg["rtt"], cfg["buf"], cfg["loss"], cfg["jitter"], seed)
    kinds = [subject if subject != "policy" else "learned"] + [bg] * k
    for kd in kinds:
        if kd == "bbr" and bbr_aggr: LIB.sim_add_flow(sim, KIND[kd], 10.0, CWND_MAX, 0.0, 1.15, 3.0)   # more aggressive BBR variant (pacing x1.15, cwnd 3 BDP)
        else: LIB.sim_add_flow(sim, KIND[kd], 10.0, CWND_MAX, 0.0, 128.0, 64.0)
    x = (ctypes.c_double * SM_IN)(); aux = (ctypes.c_double * 8)(); st = (ctypes.c_double * 8)(); iv = (ctypes.c_double * 4)()
    t = 0.0; T = 100.0; warm = 0.25 * dur; base = None; qd_sum = qd_n = 0
    while t < dur:
        LIB.sim_run(sim, T); t += T
        for j, kd in enumerate(kinds):
            if kd == "learned":
                act = LIB.sim_features(sim, j, x, aux); LIB.sim_state(sim, j, st)
                if st[5] > 0.5:
                    if aux[1] / max(st[3], 1.0) >= 1.3 or aux[2] > 0: LIB.sim_set_ss(sim, j, 0)
                elif act:
                    a, _ = policy.act(np.array(list(x)), rng, False); LIB.sim_set_cwnd(sim, j, st[0] * 2.0 ** a); LIB.sim_set_action(sim, j, a)
                if t >= warm and j == 0: qd_sum += aux[0]; qd_n += 1
            else: LIB.sim_ivstat(sim, j, iv)
        LIB.sim_state(sim, 0, st); T = min(500.0, max(100.0, st[2])) if st[2] > 0 else 100.0
        if base is None and t >= warm:
            base = []
            for j in range(len(kinds)): LIB.sim_state(sim, j, st); base.append(st[4])
    fin = []
    for j in range(len(kinds)): LIB.sim_state(sim, j, st); fin.append(st[4] - base[j])
    LIB.sim_free(sim)
    tot = sum(fin); n = len(fin); fair = tot / n
    return dict(J=jain(fin), subj=fin[0] / fair, minrel=min(fin) / fair, util=tot / (cfg["mu"] * (dur - warm) / 1000.0), qd=qd_sum / max(1, qd_n))


def evaluate(npz_list, reps=8):
    pols = {os.path.basename(p).replace(".npz", ""): FixedPolicy(p) for p in npz_list}
    subjects = [(n, "policy", pl) for n, pl in pols.items()] + [("aimd", "aimd", None), ("cubic", "cubic", None), ("bbr", "bbr", None)]
    rows = []
    print("%-14s %-6s %2s %6s %7s %7s %6s %6s" % ("subject", "bg", "k", "J", "subj", "min", "util", "qdelay"))
    for name, kind, pl in subjects:
        for bg in ("cubic", "bbr"):
            for k in (1, 2, 3):
                rs = [eval_pair(kind, bg, k, 100 + r, pl) for r in range(reps)]
                m = {q: float(np.mean([r[q] for r in rs])) for q in rs[0]}
                print("%-14s %-6s %2d %6.3f %7.2f %7.2f %6.2f %6.2f" % (name, bg, k, m["J"], m["subj"], m["minrel"], m["util"], m["qd"]))
                rows.append((name, bg, k, m))
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iters", type=int, default=300); ap.add_argument("--episodes", type=int, default=24)
    ap.add_argument("--w4", type=float, default=1.5, help="fairness penalty weight (0 = ablation without fairness)")
    ap.add_argument("--w1", type=float); ap.add_argument("--w2", type=float); ap.add_argument("--w3", type=float)
    ap.add_argument("--overshare", type=float, help="weight of the one-sided over-share term inside the fairness penalty (default 0.5)")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--bbr-random", action="store_true", help="randomise the BBR competitor's gains in training")
    ap.add_argument("--bbr-weight", type=float, default=0.25, help="probability mass of BBR among competitors"); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--tag", default="fair"); ap.add_argument("--export", help="npz -> src/neural_weights.h")
    ap.add_argument("--eval", nargs="+", help="npz files to evaluate in the simulator")
    ap.add_argument("--reps", type=int, default=8)
    a = ap.parse_args()
    if a.export: export_header(a.export, os.path.join(ROOT, "src", "neural_weights.h"))
    elif a.eval: evaluate(a.eval, a.reps)
    else: train(a)
