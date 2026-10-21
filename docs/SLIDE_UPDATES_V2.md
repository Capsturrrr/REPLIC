# Slide updates (v2): paste into the deck

Only two slides change. Keep the v1 results slides as they are: the FEC numbers belong to the v2 sandbox (`replic_v2_sandbox.zip`), not to the submitted v1 repo, so label them "v2 extension" on the slide.

---
## Slide A (Results): "Forward error correction: a safety net under heavy loss"
Kicker: Results (v2 extension)

**Title (claim):** FEC shortens transfers by up to 61% at 10% loss, and costs bandwidth on clean links

**Chart (bars, completion time in s, 256 kB, 1,200 B packets, UDP impairment proxy):**
| Policy | 2% loss (n=10) | 10% loss |
|---|---|---|
| Tuned AIMD | 1.50 | 6.54 (n=20) |
| Tuned AIMD + FEC | 1.37 | 3.62 (n=20) |
| Neural | 1.27 | 5.39 (n=10) |
| Neural + FEC | 1.09 | 2.12 (n=10) |

**Bullets**
- At 10% loss: AIMD 6.5 s to 3.6 s (-45%, Welch t = 3.99, p = 0.0006); neural 5.4 s to 2.1 s (-61%, p = 0.0002)
- At 2% loss: -8% and -14%, within noise (p = 0.57, 0.36): no effect claimed
- On clean links FEC is pure overhead: 12.5% extra packets for nothing
- One XOR parity packet per 8 data packets repairs any single loss in the group without a retransmission

**Small note:** UDP impairment proxy on one machine; kernel TCP not yet compared.

**Speaker notes (about 60 s):**
"We added forward error correction in a sandbox version. One parity packet per eight data packets lets the receiver rebuild a single lost packet without waiting a round trip. At ten percent loss that cuts the neural agent's time by sixty-one percent and tuned AIMD's by forty-five, and both are statistically significant. At two percent loss the effect is inside the noise, so we do not claim it, and on a clean link it is simply wasted bandwidth. The trade-off is the point: it is a safety net for bad links, not a free speed-up. We also corrected an earlier four-run estimate that had shown fifty-five percent at two percent loss; the ten-run study does not support it."
If asked: "Is it faster than TCP?" - Not measured yet; kernel TCP under netem is the next run. "Why is the gain bigger for neural?" - With FEC the policy is no longer slowed by loss recovery; without FEC AIMD and neural were statistically indistinguishable at 10% loss in these runs, so most of the gain is FEC, not the policy.

---
## Slide B (Protocol Reliability): "Teardown is reliable now: a lost FIN no longer loses data"
Kicker: Protocol reliability (found by the new test orchestrator)

**Title (claim):** Every transfer is byte-identical after the teardown fix

**Bullets**
- Byte-for-byte check on every run exposed a defect: the closing FIN was sent once
- A lost FIN left the server's output file unflushed: 5 failures in about 150 runs, all at 10% loss
- Fix: retransmit the FIN every 150 ms until its ACK arrives (max 12 tries)
- After the fix: 40 of 40 runs byte-identical at 10% loss
- Same fix applied to the submitted v1 sender (teardown only, timings unaffected)

**Congestion-safety panel (right side):**
- Window is bounded by construction: w <= beta x BDP-estimate + queue allowance, so the standing queue is bounded
- AIMD takes over when an interval loses 10% or more (implemented)
- Not a formal stability proof; the bound assumes the BDP estimate does not overestimate

**Speaker notes (about 50 s):**
"Our test orchestrator checks every delivered file byte for byte, and that found a real bug: the client sent its final FIN once. At ten percent loss about one in ten of those packets is lost, the server never closed, and the end of the file stayed in its buffer. All the data had been delivered, but the file on disk was short. The client now retransmits the FIN until it is acknowledged, and forty of forty runs afterwards were byte-identical. On safety: we do not claim a Lyapunov proof. We bound the window by an estimate of the bandwidth-delay product plus a queue allowance, so the standing queue is bounded by construction, provided the estimate does not overshoot; the implemented safety mechanism today is the hand-over to AIMD."
If asked: "Does this change your v1 results?" - No; teardown happens after the timer stops. "Why 1 GB not tested?" - The sender is capped at about 78 MB per transfer and larger runs are rejected rather than faked.
