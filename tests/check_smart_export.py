#!/usr/bin/env python3
"""Checks that the C forward pass built from src/neural_weights.h agrees with the NumPy forward pass of the same weights.
   Usage: python3 tests/check_smart_export.py [results/smart/smart_final.npz]"""
import os, subprocess, sys, tempfile
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import train_smart_poise as T
npz = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results", "smart", "smart_final.npz")
rng = np.random.default_rng(7)
X = np.concatenate([rng.uniform(-1, 1, (200, T.SM_IN)), rng.uniform(0, 1, (200, T.SM_IN))])
src = '#define SM_HAVE_BUILTIN\n#include "%s/src/smart_cc.h"\nint main(){sm_net_t n; sm_builtin(&n); double x[SM_IN]; while(1){for(int i=0;i<SM_IN;i++) if(scanf("%%lf",&x[i])!=1) return 0; printf("%%.9f\\n", sm_forward(&n,x));}}' % ROOT
d = tempfile.mkdtemp(); c = os.path.join(d, "t.c"); open(c, "w").write(src)
subprocess.run(["gcc", "-O2", "-o", os.path.join(d, "t"), c, "-lm"], check=True)
out = subprocess.run([os.path.join(d, "t")], input="\n".join(" ".join("%.9f" % v for v in r) for r in X), capture_output=True, text=True).stdout.split()
pol = T.FixedPolicy(npz); ref = np.array([pol.act(x, None)[0] for x in X]); got = np.array([float(v) for v in out])
err = float(np.abs(ref - got).max())
print("max |C - numpy| over %d inputs: %.2e  %s" % (len(X), err, "OK" if err < 1e-5 else "MISMATCH (is src/neural_weights.h the export of this npz?)"))
sys.exit(0 if err < 1e-5 else 1)
