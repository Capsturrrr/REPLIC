#!/usr/bin/env python3
"""Interpretability and verification study of the 5-32-32-5 neural congestion controller.
Inputs: /tmp/sl_train.csv, /tmp/sl_test.csv (states logged by REPLIC_STATELOG: x0..x4,lo,act).
Outputs: results/interp/*.txt, results/interp/tree.txt (REPLIC_TREE format)."""
import numpy as np, sys
from sklearn.tree import DecisionTreeClassifier, export_text
v = np.array(open("results/dqn_chat.txt").read().split(), float); o = 0
def take(n, shape): global o; a = v[o:o+n].reshape(shape); o += n; return a
W1 = take(5*32, (5,32)); b1 = take(32, 32); W2 = take(32*32, (32,32)); b2 = take(32, 32); W3 = take(32*5, (32,5)); b3 = take(5, 5)
def q(x): return np.maximum(np.maximum(x@W1+b1,0)@W2+b2,0)@W3+b3
def act(x, lo): s = q(x); s = np.where(np.arange(5)[None,:] < lo[:,None], -1e18, s); return s.argmax(1)
def load(p): a = np.loadtxt(p, delimiter=","); return a[:,:5], a[:,5].astype(int), a[:,6].astype(int)
Xtr, LOtr, Atr = load("/tmp/sl_train.csv"); Xte, LOte, Ate = load("/tmp/sl_test.csv")
out = []
def P(s=""): print(s); out.append(s)
P(f"states: train {len(Xtr)}, test {len(Xte)} (visited by the agent on the five named paths, 20 seeds each, disjoint seeds)")
P(f"python re-implementation reproduces the C agent's decision on {100*np.mean(act(Xte,LOte)==Ate):.2f}% of test states")
names = ["RTT growth","loss fraction","window/32","throughput share","last action"]
P("\nAction mix (0=x0.5 1=x0.85 2=hold 3=x1.15 4=x1.5): " + ", ".join(f"{k}:{100*np.mean(Ate==k):.0f}%" for k in range(5)))
# 1. sensitivity
P("\n1. Input sensitivity on visited test states")
P("   permutation: share of decisions that change when ONE input is shuffled across states; delta: share that change when the input moves by +-0.1")
P("   input             permute   +0.1   -0.1")
rng = np.random.default_rng(0)
for i in range(5):
    Xp = Xte.copy(); Xp[:,i] = rng.permutation(Xp[:,i]); pp = np.mean(act(Xp,LOte)!=Ate)
    r = []
    for d in (0.1,-0.1):
        Xd = Xte.copy(); Xd[:,i] += d; r.append(np.mean(act(Xd,LOte)!=Ate))
    P(f"   {names[i]:17s} {100*pp:5.1f}%  {100*r[0]:5.1f}% {100*r[1]:5.1f}%")
# 2. distillation
P("\n2. Distillation into a decision tree (fidelity = agreement with the network on held-out states)")
P("   depth leaves  fidelity(test)  fidelity(train)")
best = None
for d in (1,2,3,4,5,6,8):
    t = DecisionTreeClassifier(max_depth=d, random_state=0).fit(Xtr, Atr)
    pa = np.maximum(t.predict(Xte), LOte)
    P(f"   {d:5d} {t.get_n_leaves():6d}  {100*np.mean(pa==Ate):9.2f}%  {100*t.score(Xtr,Atr):10.2f}%")
    if d == 5: best = t
t = best
P("\n   Depth-5 tree as rules:"); P("\n".join("   "+l for l in export_text(t, feature_names=["rtt_growth","loss","cwnd32","thr","last_act"]).splitlines()))
def export(t, path):
    tr = t.tree_; cls = t.classes_; lines = []
    def emit(n):
        idx = len(lines); lines.append(None)
        if tr.children_left[n] == -1: lines[idx] = f"L {int(cls[np.argmax(tr.value[n])])}"
        else:
            l = emit(tr.children_left[n]); r = emit(tr.children_right[n]); lines[idx] = f"S {tr.feature[n]} {tr.threshold[n]:.6f} {l} {r}"
        return idx
    emit(0); open(path, "w").write("\n".join(lines) + "\n")
export(t, "results/interp/tree_d5.txt")
export(DecisionTreeClassifier(max_depth=8, random_state=0).fit(Xtr, Atr), "results/interp/tree_d8.txt")
# 3. interval bound propagation
P("\n3. Certification by interval bound propagation (IBP): is the chosen action provably unchanged for ANY input within +-eps (L-inf, all five inputs)?")
def ibp(lo_, hi_):
    def lin(l,h,W,b): c=(l+h)/2; r=(h-l)/2; return c@W+b-r@np.abs(W), c@W+b+r@np.abs(W)
    l,h = lin(lo_,hi_,W1,b1); l,h = np.maximum(l,0),np.maximum(h,0)
    l,h = lin(l,h,W2,b2); l,h = np.maximum(l,0),np.maximum(h,0)
    return lin(l,h,W3,b3)
def certified(x, lo, a, eps):
    l,h = ibp(x-eps, x+eps)
    for k in range(lo, 5):
        if k != a and h[k] >= l[a]: return False
    return True
sub = rng.choice(len(Xte), 1500, replace=False)
P("   eps     IBP-certified   empirically stable (200 random points in the ball)")
for eps in (0.001,0.003,0.01,0.03,0.1):
    c = np.mean([certified(Xte[i], LOte[i], Ate[i], eps) for i in sub])
    e = []
    for i in sub[:300]:
        pts = Xte[i] + rng.uniform(-eps, eps, (200,5)); e.append(np.all(act(pts, np.full(200,LOte[i]))==Ate[i]))
    P(f"   {eps:5.3f}   {100*c:8.1f}%        {100*np.mean(e):8.1f}%")
open("results/interp/interpretability.txt","w").write("\n".join(out)+"\n")
