#!/usr/bin/env python3
"""BBR-style baseline vs tuned AIMD, CUBIC and the neural agent (virtual time, paired seeds, 95% t-interval).
Part A: five named scenarios.  Part B: random-loss sweep on a fixed 1000 pkt/s, 40 ms path (BBRv3 is reported to collapse at >2% random loss)."""
import subprocess, sys, statistics as st, math
from concurrent.futures import ThreadPoolExecutor
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
T = {10:2.228,20:2.093,30:2.045}.get(N, 2.1)
ARMS = {"aimd":"--mode aimd --maxcwnd 128 --ssthresh 128","cubic":"--mode cubic","bbr":"--mode bbr","deep":"--mode deep --dfile results/dqn_chat.txt"}
NAMED = {"5g":"--delay 10 --jitter 3 --loss 0.2 --rate 1500 --queue 60","lossy":"--delay 25 --jitter 10 --loss 2 --rate 800 --queue 50",
 "sat":"--delay 150 --jitter 10 --loss 0.3 --rate 400 --queue 100","fast":"--delay 60 --jitter 5 --loss 0.1 --rate 3000 --queue 300","bad":"--delay 40 --jitter 5 --loss 10 --rate 600 --queue 40"}
def run(a, sc, seed, pk=3000):
    o = subprocess.run(f"./bin/smart_client {ARMS[a]} --vt 1 {sc} --packets {pk} --seed {seed}", shell=True, capture_output=True, text=True).stdout
    for l in o.splitlines():
        if l.startswith("RESULT"): return float(l.split(",")[3])/1000
    sys.stderr.write(f"FAILED run {a} {sc} seed {seed}: {o[-200:]}\n"); return 600.0
def table(title, scs):
    print(title); lines=[title]
    with ThreadPoolExecutor(2) as ex:
        for name, sc in scs.items():
            row=f"{name:9s}"
            for a in ARMS:
                x=list(ex.map(lambda s: run(a, sc, 424242+s), range(N))); m=st.mean(x); h=T*st.stdev(x)/math.sqrt(N)
                row+=f"  {a}: {m:6.2f}s ±{h:4.2f}"
            print(row, flush=True); lines.append(row)
    return lines
L = table("A. named scenarios (completion time, 3000 packets)", NAMED)
L += table("B. random-loss sweep (1000 pkt/s, 40 ms one-way, queue 100)", {f"loss {p}%": f"--delay 40 --jitter 3 --loss {p} --rate 1000 --queue 100" for p in (0.1,0.5,1,2,3,5,10)})
open("results/bbr_compare.txt","w").write(f"# {N} paired seeds, test seed 424242+\n"+"\n".join(L)+"\n")
