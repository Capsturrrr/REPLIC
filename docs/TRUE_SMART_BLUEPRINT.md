# Poise: natively multi-objective, PPO-trained congestion control

## Pipeline

    train_smart_rudp.py ──ctypes──> sim/libnetsim.so (sim/netsim.c)   packet-level multi-flow training simulator
            │                              │ both call the SAME feature code
            │                              ▼
            │                       src/smart_cc.h   state features + tanh-MLP forward pass
            ▼                              ▲
    results/smart/smart_final.{npz,txt} ──export──> src/neural_weights.h ──compiled into──> src/rudp_sender.c  (--mode smart)

* **State (8)**: SRTT gradient, queue-delay derivative, queue-delay level, loss ratio (last window), in-flight/BDP,
  competing-traffic pressure (Fano factor of ACK counts in 10 ms bins), previous action, log2(cwnd).
* **Action**: a in (-1,1); C = 2^a in (0.5, 2.0); cwnd <- clamp(cwnd * C, 2, 128). Deterministic at run time.
* **Reward**: `R = w1*Throughput - w2*Delay - w3*Loss - w4*Fairness`, Fairness = g * [(1-J) + 0.5*max(0, s_i-1)] with Jain's index J
  over all active flows, s_i the flow's share relative to the equal share, g a link-saturation gate (see paper Sec. III).
  Reported agent: w = (1, 0.2, 3, 1.5).
* **Algorithm**: PPO (numpy, hand-written backprop; PyTorch is not needed and was not installed), asymmetric critic that also sees scenario
  descriptors, 32 episodes x 300 iterations (about 8 minutes on 2 cores), checkpoint chosen on held-out simulator seeds.
* **Deployment**: no Python, no heap, ~1.4k multiply-adds per control interval (100-500 ms). `--mode smart` has no `--polite` rule and no AIMD hand-over.

## Reproduce

    python3 train_smart_rudp.py --iters 300 --episodes 32 --w2 0.2 --w4 1.5 --tag fa       # fairness-trained
    python3 train_smart_rudp.py --iters 300 --episodes 32 --w2 0.2 --w4 0   --tag nofa     # ablation
    python3 train_smart_rudp.py --eval results/smart/smart_fa_best.npz results/smart/smart_nofa_best.npz   # simulator table
    cp results/smart/smart_fa_best.npz results/smart/smart_final.npz
    python3 train_smart_rudp.py --export results/smart/smart_final.npz                     # writes src/neural_weights.h
    python3 tests/check_smart_export.py                                                    # C forward == numpy forward
    python3 run_fairness_test.py --subjects smart_fair,smart_nofair --reps 5               # real UDP stack, shared bottleneck
    python3 studio/app.py                                                                  # live dashboard, Coexistence arena tab

Training is seeded but not bit-reproducible across machines; expect the same behaviour, not the same weights.

## Measured results (real UDP stack through the impairment proxy, 5 runs per cell, seeds 1-5)

| background | k | Static agent J / share | Static + guard J / share | Poise J / share | no-fairness ablation J / share |
|---|---|---|---|---|---|
| CUBIC | 1 | 0.957 / 1.20 | 0.911 / 0.72 | **0.982 / 0.88** | 0.879 / 1.37 |
| CUBIC | 2 | 0.853 / 1.58 | 0.940 / 0.80 | **0.969 / 1.09** | 0.788 / 1.72 |
| CUBIC | 3 | 0.827 / 1.73 | 0.951 / 0.75 | **0.968 / 1.03** | 0.708 / 2.08 |
| BBR-style | 1 | 0.935 / 0.91 | 0.878 / 0.64 | 0.935 / 0.78 | 0.897 / 1.31 |
| BBR-style | 2 | 0.879 / 0.92 | 0.830 / 0.62 | 0.818 / 0.64 | 0.713 / 0.99 |
| BBR-style | 3 | 0.787 / 1.33 | 0.865 / 0.66 | 0.779 / 0.67 | 0.840 / 1.16 |

Significant (Welch): Poise vs static, J at CUBIC k=2,3 (p=0.001, <0.001); share at all CUBIC k (p<=0.001); Poise vs ablation, J at all CUBIC k (p<=0.014).
Not significant: Poise vs the guard on J (p=0.11-0.30); all BBR comparisons.

## What does not work, and what is not done
* **BBR-style competitors**: no improvement; the agent yields to them (0.64-0.78x share). The simulator predicted better, so the real BBR-style sender is harsher
  than the simulated one. A retrain against randomised aggressive simulated BBR was worse in validation and was dropped.
* A first version without log2(cwnd) in the state could not learn fairness (a multiplicative policy cannot realise additive increase); kept as an ablation
  (`results/smart/ablation_nocwnd_*.csv`).
* Competitors are user-space CUBIC/BBR inside RUDP; the kernel-TCP backend of `run_fairness_test.py` has not been executed (no `tc`/`ip` here).
* Slow start, pacing, retransmission timers and window bounds are fixed rules, not learned.
* Single-flow cost (256 kB, n=8): faster than tuned AIMD on a clean path (527 vs 599 ms, p<0.001), equal at 2% and 10% loss; static agent + FEC is still best at 10% loss.
