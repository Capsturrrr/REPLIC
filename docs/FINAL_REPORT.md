# REPLIC: Reinforcement-Learning-Engineered Protocol for Internet Congestion

Final project report

Harshavardhan Mallela (24BAI0274), Rohit Arya (24BCE0819), Shloak Sinha (24BDS0378). Computer Networks, VIT Vellore. Faculty: Anuradha G.
Report date: 13 October 2026. Every number below is regenerated from the raw CSVs by `make_report_figures.py`; figures are in `docs/figures_v2/`.

## 1. Summary

REPLIC is a reliable transport built on UDP in C. REPLIC is its learned congestion controller: a small neural network (8 inputs, two hidden layers of 32, one output) trained with PPO and compiled into a C header, so it needs no runtime dependencies.

What the evidence shows, strongest first:

- **High-latency lossy (cellular-like) paths.** REPLIC moves 1 MB in 6.1 s on average. Original AIMD (window 32) needs 14.9 s, tuned AIMD (window 128) 12.9 s, user-space CUBIC 12.5 s and the REPLIC BBR-style baseline 6.6 s. REPLIC with FEC needs 5.2 s. Paired on the same 30 paths, REPLIC finishes in 0.41x the time of original AIMD and 0.50x that of tuned AIMD, and wins on 30 of 30 paths against each.
- **Worst case.** The 95th-percentile transfer takes 8.5 s with REPLIC and 29.8 s with user-space CUBIC.
- **Fairness.** Against CUBIC competitors the Jain index is 0.974 (one competitor) and 0.958 (two), against 0.942 and 0.881 for the static agent. The same network trained without the fairness term takes up to 1.72x its fair share.
- **Recovery upgrade.** A SACK scoreboard, tail-loss probe and jitter-aware slow-start exit cut transfer time by 9 to 30 percent when ACKs are also lost, and removed a 2.6x slowdown on datacenter micro-bursts (499 ms to 185 ms, level with CUBIC at 194 ms).
- **Correctness.** 870 proxy-matrix runs, every one byte-identical to the source file. Testing found and fixed a real protocol bug (lost SYN-ACK).

What it does not show: **kernel TCP is faster than REPLIC on Linux netem in every profile we ran** (about 1.3 to 1.9x faster than kernel CUBIC on the four profiles in Figure 10). REPLIC with FEC ties kernel CUBIC on the cellular family; kernel BBR stays ahead. All other baselines are our own user-space implementations. See section 6.

## 2. What was built

| Part | What it is |
|---|---|
| Protocol | 13-byte header (SYN, ACK, FIN, DATA), 1200-byte payload, Selective Repeat with a 256-packet SACK bitmap, RTO with Jacobson estimator and Karn's rule |
| FEC | One XOR parity packet per 8 data packets repairs a single loss without a round trip |
| REPLIC | PPO-trained MLP 8-32-32-1, about 1,300 multiplications per decision, action = window x 2^tanh(o), reward with a Jain-fairness term |
| Baselines | AIMD (window 32 and tuned 128), CUBIC, a simplified BBR, the first neural agent (DQN, "Neural v1") |
| Studio | Web dashboard that races policies on an impaired link |
| Drop | File-transfer app on REPLIC with a byte-exact SHA-256 check |
| Network lab | Wireshark dissector for the REPLIC header and a Packet Tracer OSPF topology |

## 3. Method

- **Backends.** A UDP impairment proxy (`bin/impair`: forward loss, delay both ways, rate, queue, cross traffic, optional ACK-path loss) and Linux netem plus tbf on loopback (impairs both directions; also runs kernel TCP CUBIC and BBR). The two backends are never mixed in one table.
- **Path families.** Cellular: 40 to 120 ms one-way delay, 10 to 40 ms jitter, 0.5 to 3% loss, 150 to 600 kB/s, 20 to 50 kB buffer. Campus: 2 to 8 ms, up to 0.1% loss, 2.5 to 5 MB/s. Datacenter micro-burst: sub-millisecond delay, 8 to 12 MB/s, shallow buffer, line-rate bursts. Seed s draws one concrete path from the family, so path variance is part of every cell.
- **Paired design.** Every policy sees exactly the same path for the same seed. Comparisons are geometric-mean ratios with a t-interval on the log ratios, paired t-test, Wilcoxon test and Holm correction. Median and p95 use bootstrap intervals; p99 is a point estimate at n=30.
- **Verification.** Each run's output is compared byte for byte with the source file. Failed runs are kept in `raw.csv` with `ok=0` and counted, never dropped silently.

## 4. Results

| Figure | File | Finding |
|---|---|---|
| 1 | `01_cellular_mean.png` | Mean 1 MB time on cellular paths: REPLIC 6.1 s, REPLIC+FEC 5.2 s, BBR-style 6.6 s, CUBIC 12.5 s, tuned AIMD 12.9 s, original AIMD 14.9 s |
| 2 | `02_per_path_scatter.png` | REPLIC beats both AIMD builds on 30 of 30 paths (ratios 0.41 and 0.50) |
| 3 | `03_paired_forest.png` | Versus CUBIC: cellular 0.57x (CI 0.47 to 0.68), cellular with FEC 0.49x, campus 0.94x, datacenter 0.96x (tie) |
| 4 | `04_tails_box.png` | p95: REPLIC 8.5 s, REPLIC+FEC 7.0 s, CUBIC 29.8 s |
| 5 | `05_cdf.png` | Medians: REPLIC 5.8 s, CUBIC 10.4 s, tuned AIMD 12.3 s |
| 6 | `06_campus_datacenter.png` | Campus and datacenter means with 95% CI |
| 7 | `07_datacenter_fix.png` | Datacenter: 499 ms before the recovery upgrade, 185 ms after, CUBIC 194 ms |
| 8 | `08_recovery_upgrade.png` | 9 to 30% faster with ACK loss (4 runs per cell) |
| 9 | `09_fairness.png` | Jain index 0.974 and 0.958 with REPLIC against 0.942 and 0.881 for the static agent |
| 10 | `10_netem_vs_kernel.png` | Linux netem: kernel TCP faster than REPLIC on all four profiles |
| 11 | `11_netem_replic_speedup.png` | Linux netem: REPLIC 1.8 to 3.3x faster than user-space AIMD, 2.9 to 3.9x faster than user-space CUBIC, 0.9 to 1.3x against the REPLIC BBR-style baseline |
| 12 | `12_evidence.png` | 870 byte-verified runs |

Paired statistics, cellular family, ratio of completion time (95% CI):

| REPLIC policy | vs original AIMD | vs tuned AIMD | vs CUBIC |
|---|---|---|---|
| REPLIC | 0.41 (0.38 to 0.45), 30/30 | 0.50 (0.44 to 0.55), 30/30 | 0.57 (0.47 to 0.68), 25/30 |
| REPLIC + FEC | 0.36 (0.32 to 0.40), 30/30 | 0.43 (0.38 to 0.48), 30/30 | 0.49 (0.41 to 0.58), 28/30 |

Tuning helps AIMD by about 17% (ratio 0.83, faster on 23 of 30 paths); REPLIC is still twice as fast as the tuned build.

## 5. Problems found and what fixed them

| Problem | How it was found | Fix |
|---|---|---|
| Lost SYN-ACK: the server treated the client's retransmitted SYN as a new session and the file arrived empty | Netem tests that also drop ACKs | Server recognises the repeated ISN and re-sends the SYN-ACK; regression test `tests/test_synack_loss.sh` |
| Lost FIN left the output file unflushed | Fault injection | FIN is retransmitted until ACKed |
| REPLIC 2.6x slower than CUBIC on datacenter micro-bursts | Paired n=30 matrix | SACK scoreboard loss detection, tail-loss probe, jitter-aware slow-start exit; now level with CUBIC |
| First slow-start exit rule was too eager (REPLIC 18% slower on LTE) | A/B against the old sender | Exit at 1.25 x min RTT + 4 x RTT variance; verified no regression |
| Benchmark harness port collisions and 2-minute tool limits | Failed runs in the log | Sequential runs, resumable runner, failed rows removed and re-run |

## 6. Honest limits

- **Kernel TCP is faster on netem.** On the WSL2 netem matrix (10 runs per cell, sender before the upgrade) kernel CUBIC and BBR beat REPLIC in every profile. REPLIC with FEC ties kernel CUBIC (0.97x) on the cellular family only.
- **The headline baselines are our own.** AIMD, CUBIC and BBR in the charts are user-space implementations on the same transport. That is a fair comparison of congestion control, not a claim about the Linux kernel.
- **The recovery upgrade has only been measured on the proxy** (4 runs per cell in the ACK-loss study) and has not been re-run against kernel TCP. In one A/B cell (LTE, n=8) the upgraded REPLIC was slower (2.7 s against 2.3 s), so the gain is not uniform.
- **Control interval.** REPLIC decides every 100 ms of smoothed RTT or longer; a per-ACK controller would need retraining.
- **Fairness.** No gain against BBR-style competitors; REPLIC yields (about 0.74 of its equal share).
- **Simulator mismatch.** REPLIC was trained in a simulator. An older DQN agent beat REPLIC on netem, which points to a gap between the simulator and real impairment.
- **Netem tail profile.** At 20% loss several runs did not complete, probably hitting the 200 s limit; those are counted, not hidden.
- **The path families are our own parameter ranges**, not measurements of real networks.

## 7. Reproduce

```
make                                   # builds bin/
python3 run_scale_matrix.py --families cellular_highlat --policies aimd,aimd32,cubic,bbr,smart,smart+fec --n 30 --out results/scale/proxy_v2a
python3 make_report_figures.py         # rebuilds docs/figures_v2/*.png from the raw CSVs
sudo python3 run_scale_matrix.py --backend netem   # WSL/Linux with netem: adds kernel TCP rows
bash tests/test_synack_loss.sh         # regression test for the lost SYN-ACK bug
```

## 8. Next steps

Re-run the REPLIC policies on netem with the upgraded sender; try a 20 to 30 ms control interval and stricter pacing; retrain with netem-like impairment; add the Packet Tracer file to `network/`; write the case study and record the video by 21 October.
