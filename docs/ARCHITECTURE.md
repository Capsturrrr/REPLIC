# REPLIC architecture

REPLIC stands for Reinforcement-Learning-Engineered Protocol for Internet Congestion.

REPLIC is a reliable, congestion-controlled transport over UDP, written in C, with a learned congestion controller and the tooling
to evaluate it honestly. This page is the map; `README.md` has the commands and `docs/REPLIC_Final_Project_Report.pdf` the full evidence.

## Layers

```
 application      chat (terminal / web)        file transfer (smart_client --file)
 ─────────────────────────────────────────────────────────────────────────────────
 transport        reliability: Selective Repeat, 256-packet window, cumulative ACK + 32-byte bitmap,
 (src/)           per-packet timers, fast retransmit, adaptive RTO (RFC 6298, floor 1.3 x srtt)
                  congestion control (pluggable): fixed | AIMD | CUBIC | table Q | neural DQN (+ AIMD hand-over)
 ─────────────────────────────────────────────────────────────────────────────────
 wire             13-byte header: seq(4) ack(4) flags(1) CRC-16(2) len(2) | payload (<= 512)
 ─────────────────────────────────────────────────────────────────────────────────
 UDP / IP         real sockets
```

## Components

| Path | What it is |
|---|---|
| `src/common.[ch]` | Wire format: pack / unpack, CRC-16/CCITT-FALSE. Fuzzed (`make fuzz`) and unit tested. |
| `src/server.c` | Receiver. Handshake, Selective Repeat reassembly, ACK + bitmap, file output (`REPLIC_OUT`), integrity hash. `-DREPLIC_GBN` builds the Go-Back-N receiver for comparison. |
| `src/client.c` | The original teaching client (AIMD, 62 packets). |
| `src/replic.h`, `src/replic_sender.c` | **libreplic**: the sender as a library with a small public API (`replic_send`, `replic_send_file`, options and stats structs). Holds the reliability machinery, the congestion controllers, the in-process path emulation and the virtual-time simulator. Process-global state, one transfer at a time. |
| `src/smart_client.c` | Command-line front end of the library; the evaluation scripts drive this program. Output is one `RESULT` line. |
| `src/deep_cc.h`, `src/rl_cc.h` | Neural (5-32-32-5) and tabular agents, inference only. Weights are plain text in `results/`. |
| `src/impair.c` | User-space network impairment proxy (delay, jitter, loss, shared bottleneck with drop-tail queue) for tests over real sockets without root. |
| `src/chat.c`, `web/replic_web.py` | Chat over the same transport (terminal and web gateway with live dashboard). Interoperate with each other (`tests/chat_interop.sh`). |
| `web/` (Python) | Training (`train_deep.py`, `es_train.py`), simulators, statistics, the virtual-time harness (`vt.py`, `vt_random.py`). |
| `network/` | Cisco Packet Tracer design (four-router OSPF), Wireshark dissector `replic.lua`, exported traces. |
| `tests/` | `make test`: wire format, end-to-end delivery (both receivers), handshake regression, file integrity through the proxy, gateway/chat interop, simulator. |

## One send cycle (smart_client)

1. Release packets whose emulated arrival time has come (or send on the real socket).
2. Read ACKs; cumulative ACK advances `base`, bitmap marks selectively acknowledged packets; RTT sample from first-transmission packets only (Karn).
3. Window policy reacts: AIMD / CUBIC on every ACK; the neural agent once per decision interval (clamp(srtt, 100, 500) ms) from five inputs
   (RTT ratio, loss fraction, window, throughput, last action), choosing one of five window multipliers.
4. Per-packet retransmission timers fire for unreported packets; the window is penalised once per loss event (`recover`).
5. New packets are sent while `next < base + cwnd`, paced at 2 x cwnd / srtt for the neural agent.

Neural-agent safeguards: classic slow start to 64 packets before the policy takes over, never shrink without a congestion signal,
hand control to AIMD while the loss fraction per interval is at least 10%.

## Evaluation methodology

* **Three environments for one sender.** (1) in-process emulator with a real clock and a real server process, (2) the same sender on real
  UDP sockets through the impairment proxy, (3) virtual time (`--vt 1`): same sender code, simulated receiver and clock, about 1000x faster.
  Cells of (1) and (3) agree within their 95% intervals (`web/vt_calib.py`); (2) agrees with (1).
* **Baselines are tuned**, not strawmen: AIMD with cap 128 and threshold 128, and CUBIC with cap 128.
* **Paired seeds, 95% t intervals**, plus a generalisation test on randomly drawn paths that were not used for tuning (`web/vt_random.py`).
* **Negative results are kept** (hostile 10% loss path, fairness, CUBIC on fast paths, dropped ideas) in the report.
