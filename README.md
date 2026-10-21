# REPLIC: Reinforcement-Learning-Engineered Protocol for Internet Congestion

REPLIC is a reliable UDP transport written in C with a learned (PPO) congestion controller.

[![ci](https://github.com/Capsturrrr/replic/actions/workflows/ci.yml/badge.svg)](https://github.com/Capsturrrr/replic/actions/workflows/ci.yml)

A reliable, congestion-controlled transport over UDP, written in C: handshake, Selective Repeat with a 256-packet window,
CRC-16, adaptive retransmission timer, pluggable congestion control (AIMD, CUBIC, tabular Q-learning, a small neural network),
file transfer, a chat application (terminal and web), a four-router OSPF network design, a Wireshark dissector, and the evaluation
tooling to test all of it honestly.

**Team:** Harshavardhan Mallela (24BAI0274), Rohit Arya (24BCE0819), Shloak Sinha (24BDS0378). Computer Networks, VIT Vellore, faculty Anuradha G.

## Final report and figures (v2)

* `docs/FINAL_REPORT.md`: the final report (results first, then problems found, then honest limits).
* `docs/figures_v2/`: 12 figures regenerated from the raw CSVs by `python3 make_report_figures.py`.
* REPLIC vs AIMD on 30 high-latency cellular paths (paired): 0.41x the time of original AIMD (window 32) and 0.50x that of tuned AIMD (window 128), faster on 30 of 30 paths. Kernel TCP is still faster than REPLIC on Linux netem; see the report's section 6.

## Headline results

Completion time of a transfer, neural agent against **tuned** baselines (AIMD with window cap 128, CUBIC with cap 128), same transport,
same timers, paired seeds, 95% intervals. Details and caveats in `docs/REPLIC_Final_Project_Report.pdf`, Section 8.11 to 8.13.

| Path | AIMD tuned | CUBIC tuned | Neural agent | vs AIMD |
|---|---|---|---|---|
| 5G-like | 1.09 s | 1.19 s | **0.92 s** | -15% |
| Lossy, 2% loss | 7.05 s | 13.92 s | **3.62 s** | -49% |
| Satellite-like | 11.36 s | 8.26 s | **5.46 s** | -52% |
| Fast, high delay (3,000 packets) | 8.20 s | 6.52 s | **3.92 s** | -52% |
| Hostile, 10% loss | 8.34 s | 12.32 s | 8.93 s | tie (n.s.) |

* The same ordering holds on **real UDP sockets** through an impairment proxy (`eval_proxy.sh`) and in a **virtual-time build of the same
  sender** (`--vt 1`, calibrated against the real-clock runs in 15 of 15 cells).
* On **100 random held-out paths** the agent needs 0.53x the time of tuned AIMD, is faster on 89 and never more than 20% slower (`web/vt_random.py`).
* Fairness knob: `--polite 1.3` makes the agent back off when a standing queue builds. Against tuned AIMD its share of a shared link falls from 69% to about 45-50%; the cost is an average of 0.58x instead of 0.53x of AIMD's time, with a worst path 2.95x slower (`results/polite_summary.txt`). Off by default. With 8 runs: share 70% to 52%, Jain 0.86 to 0.94 (`results/polite_summary.txt`).
* Bursty cross traffic (`eval_cross.sh`, proxy option `--cross ON:OFF:PPS`): the agent is 15% faster than tuned AIMD but level with CUBIC, and retransmits about 3x as much as AIMD (`results/eval_cross_summary.txt`).
* An alternative weight file trained with cross traffic in the loop (`results/dqn_cross.txt`, `--dfile`): small consistent gain in simulation, not significant on real sockets, so not the default (`results/es_fair/SUMMARY_cross.txt`). Virtual-time options: `--vcross ON:OFF:PPS` (bursty cross traffic) and `--vcomp 1` (an AIMD competitor).
* The server handles up to 16 clients at once (`tests/test_multiclient.sh`).
* Honest limits: on a shared bottleneck it takes 68% of the link against tuned AIMD (not a polite neighbour); no gain on the 10% loss path;
  kernel TCP is still 1.5x to 1.7x faster than the neural agent on a netem path (see "Kernel check"), and no multi-machine test yet. Earlier, larger margins were inflated by a timer defect that hurt
  the baseline more; it is fixed and every number above is re-measured.

## Quick start

```bash
make all smart chat            # build (gcc, make; Python 3 + numpy for the tools)
make test                      # unit, end-to-end, handshake, file integrity, chat interop, simulator
make fuzz                      # packet parser under AddressSanitizer + UBSan
make figures                   # regenerate figures/headline.png, random_paths.png, cwnd_traces.png
make repro                     # reproduce the headline table in virtual time, about 30 seconds (SEEDS=100 paired seeds)

./filetransfer_demo.sh 2 lossy # send a 2 MB random file over an impaired path with three policies, verify byte-identical
./bench_loopback.sh            # raw sender speed on loopback

# a transfer by hand
REPLIC_OUT=/tmp/out.bin ./bin/server &                     # receiver writes the file, prints length and hash
./bin/smart_client --mode deep --emu 0 --file big.bin    # sender (deep = neural agent; aimd, cubic, rl2 also available)
```

Virtual time (seconds instead of minutes, deterministic):

```bash
./bin/smart_client --vt 1 --mode deep --delay 25 --jitter 10 --loss 2 --rate 800 --queue 50 --packets 1200 --seed 1
python3 web/vt_named.py        # five named paths, 100 paired seeds
python3 web/vt_random.py 100   # 100 random held-out paths
python3 web/vt_ablate.py       # ablation in the real sender
python3 web/vt_calib.py        # virtual time vs real clock
```

## Use it as a library

The sender is a C library (`lib/libreplic.a`, header `src/replic.h`); `smart_client` is just a command-line wrapper around it.

```c
#include "replic.h"
replic_options_t o; replic_options_default(&o);
o.port = 8888; o.mode = "deep"; o.weights_file = "results/dqn_chat.txt";   // or "aimd", "cubic"
replic_stats_t st;
int rc = replic_send(&o, buf, len, &st);          // blocks until every byte is acknowledged; replic_send_file() for a file
if (rc != REPLIC_OK) fprintf(stderr, "%s\n", replic_strerror(rc));
```

```bash
make lib examples                 # lib/libreplic.a and bin/example_send
REPLIC_OUT=/tmp/out.bin ./bin/server &  ./bin/example_send 8888 some_file
sudo make install                 # libreplic.a, replic.h, replic-server, replic-send under /usr/local
```

Limits: one transfer per call (connect, send, close), up to 32 MB, not thread-safe (process-global state), IPv4 only. The refactor from the old
single program was checked by comparing 34 deterministic virtual-time results before and after: byte-for-byte identical (`tests/test_api.sh` covers the API).

## Layout

See `docs/ARCHITECTURE.md` for the design. In short: `src/` (C: wire format, server, senders, agents, impairment proxy, chat), `web/` (Python:
gateway, training, virtual-time harness, studies), `tests/`, `network/` (Packet Tracer configs, Wireshark dissector and traces), `results/`
(raw data and summaries behind every table), `docs/` (report, slides, guides), `eval_*.sh` + `ci_summary_*.py` (experiments and their summaries).

## How the evidence was produced

| Question | Script | Output |
|---|---|---|
| Final comparison, real clock | `eval_final.sh`, `ci_summary_final.py` | `results/eval_final_*.csv` |
| Same on real UDP sockets | `eval_proxy.sh`, `ci_summary_proxy.py` | `results/eval_proxy_*.csv` |
| Two flows on one bottleneck | `eval_fair.sh`, `ci_summary_fair.py` | `results/eval_fair_summary.txt` |
| Generalisation and ablation | `web/vt_random.py`, `web/vt_ablate.py`, `web/vt_named.py` | `results/vt_*.txt` |
| Evolution-strategy training in C | `web/es_train.py` | `results/es/` |
| History: Go-Back-N era, tuning steps | `eval_ci*.sh`, `eval_perf*.sh` | `results/` |

Selective Repeat is the default; Go-Back-N is kept for comparison (`--gbn`, `REPLIC_GBN=1`, `make server_gbn`). The original settings of the neural agent
(cap 32, no pacing, no slow start, no hand-over) are available with `REPLIC_CLASSIC=1`.

## Chat

```bash
./bin/chat 9001 9002 Alice 30        # terminal chat: my_port peer_port name loss%
./bin/chat 9002 9001 Bob   30
python3 web/replic_web.py --udp 9001 --peer 127.0.0.1:9002 --http 8080     # web gateway with live dashboard (see docs/CHAT_ONLINE.txt)
```
The gateway and the terminal chat speak the same protocol and are tested against each other at 20% loss in both directions.

## Network design and Wireshark

Four routers (R1 to R4), OSPF area 0, two VLANs, DHCP, an extended ACL, tested failover (70 of 71 pings during a link shutdown): configs in `network/cfg/`,
diagram `network/topology_final.png`. `network/replic.lua` is a Wireshark dissector (`wireshark -X lua_script:network/replic.lua`); see `network/WIRESHARK_STEPS.txt`.

## Status

Phases 0 to 7 (handshake, Go-Back-N, AIMD, netem, benchmarks), then REPLIC, Selective Repeat, performance work, hardening and the real-socket and
virtual-time validation described in the report. Case study and video due 21 Oct 2026.

## Setup (one-time, per machine)

This project requires a Linux environment. On Windows, use **WSL2**:

```bash
# In an Administrator PowerShell:
wsl --install -d Ubuntu
# Restart if prompted, then set up your Ubuntu username/password on first launch.
```

Inside Ubuntu/WSL:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential gcc gdb valgrind iproute2 git python3-pip
pip install matplotlib --break-system-packages
```

**Wireshark** should be installed natively on Windows (not inside WSL) — download from
wireshark.org. Packet capture on WSL's loopback interface is done via `tshark` (installed
below), with the resulting `.pcapng` file copied to Windows for viewing in the Wireshark GUI.

```bash
sudo apt install -y tshark   # say Yes to the "non-superusers capture packets" prompt
```

## Clone and build

```bash
git clone https://github.com/Capsturrrr/replic.git ~/projects/replic
cd ~/projects/replic
make
```

## Running it

**Terminal 1 (server):**
```bash
./bin/server
```

**Terminal 2 (client):**
```bash
./bin/client [loss_percent]
```
`loss_percent` is optional (default 0) — simulates packet loss client-side to test
retransmission/congestion-control behavior without needing real network emulation.
Example: `./bin/client 20` simulates 20% packet loss.

The client sends a ~2.4KB test message split into 40-byte chunks, using the sliding
window + AIMD congestion control. Watch for `TIMEOUT`, `FAST RETRANSMIT`, and `cwnd=`
lines in the output — that's the reliability/congestion logic reacting live.

## Testing under real network conditions (tc/netem)

For more realistic testing than the built-in `loss_percent` simulator:

```bash
sudo tc qdisc add dev lo root netem loss 15%     # apply 15% loss on loopback
./bin/client 0                                    # 0% built-in loss — netem provides the real loss
sudo tc qdisc del dev lo root netem                # IMPORTANT: remove when done, or it persists
```

Other useful profiles: `delay 100ms`, `delay 100ms 20ms` (jitter), `corrupt 1%`, `duplicate 1%`.

**Always run `tc qdisc del dev lo root netem` when finished** — a leftover rule silently
affects every future test and can make things look broken when they aren't.

## Capturing packets with Wireshark

Wireshark's Windows GUI can't see traffic inside WSL's internal loopback. Capture with
`tshark` from inside WSL instead, then move the file to Windows to view:

```bash
sudo tshark -i lo -f "udp port 8888" -w /tmp/capture.pcapng
# (in other terminals, run server + client as usual)
# Ctrl+C to stop capturing, then:
sudo cp /tmp/capture.pcapng ~/capture.pcapng
sudo chown $USER:$USER ~/capture.pcapng
cp ~/capture.pcapng "/mnt/c/Users/<YourWindowsUsername>/Downloads/"
```
Then open it in Wireshark on Windows via File → Open. Filter with `udp.port == 8888`.

## Generating graphs (Phase 7)

```bash
python3 plot_cwnd.py logs/cwnd_log.csv cwnd_graph.png
```
Produces a congestion-window-over-time graph with timeout/fast-retransmit events marked —
generated automatically after every client run (logged to `logs/cwnd_log.csv`).

```bash
chmod +x benchmark.sh
./benchmark.sh   # requires server running separately; sweeps loss 0–40%, ~1 min
```
Produces `logs/benchmark_results.csv` — duration and final cwnd at each loss level.

## Known gotchas (things that bit us during development)

- **Server state machine**: originally only accepted a new connection while in `LISTEN`
  state. If a client got killed mid-session (Ctrl+C, crash), the server would get stuck
  and silently reject all future connections. Fixed — the server now accepts a fresh SYN
  in any state. If you ever see repeated handshake failures for no reason, restart the
  server fresh as a first troubleshooting step.
- **Leftover netem rules** persist across terminal sessions and stack with each other —
  always `tc qdisc show dev lo` to check before assuming a test result is "real."
- **`grep -oP`** (PCRE regex) isn't reliably available in all WSL setups — the benchmark
  script uses plain `sed`/`grep` instead for portability.


## Kernel check (tc/netem)

> **Correction (12 Oct 2026):** the first kernel runs sent REPLIC's *synthetic 40-byte* packets while TCP and UDP sent 512-byte payloads. The scripts now send a real 512-byte-per-packet file and every kernel run was repeated; the text below uses the corrected numbers (`results/netem_corrected.txt`, `results/netem_coexist_summary.txt`).

`sudo bash netem_full.sh lossy|5g|sat|reorder [REPS]` repeats the comparison on the Linux kernel: kernel TCP, plain UDP and REPLIC (untuned AIMD, tuned AIMD, AIMD with a 10-packet start, neural) over a netem delay/loss stage and a token-bucket bottleneck. Against tuned AIMD the neural agent was 71% faster (lossy), 65% (5G-like) and 83% (satellite), all significant, and 60% faster with reordering (not significant, p=0.09). **Kernel TCP was faster than the neural agent on every path, by 1.5x to 1.7x.** `sudo bash netem_coexist.sh 5g|lossy|sat` puts a kernel TCP flow and one REPLIC transfer on the same bottleneck: **the neural agent does not starve TCP** (it takes 24% to 34% of the link; tuned AIMD only 5% to 17%), 3 runs per cell. Raw numbers, and an account of four mistakes in the first runs, are in `results/netem_summary.txt`.

## Research-gap tests (all in virtual time unless noted; paired seeds, 95% intervals)

| Question | Command | Result |
|---|---|---|
| Does it cope when bandwidth or delay changes mid-transfer? | `python3 tools/dyn_eval.py 20 aimd,cubic,bbr,deep` (`--dyn "T_MS:RATE_PPS:DELAY_MS;..."`) | Neural fastest in 4 of 4 cases (bandwidth drop 12.7 s vs 13.6 s AIMD; rise 4.3 s vs 9.2 s; delay jump 19.8 s vs 33.3 s; hand-over pattern 8.2 s vs 12.8 s), but with 1% to 6% retransmissions (`results/dynamic_path.txt`) |
| Against a model-based baseline? | `python3 tools/bbr_eval.py 20` (`--mode bbr`, a simplified BBR) | BBR-style beats tuned AIMD on 4 of 5 paths and is **fastest on the 2% lossy path** (8.2 s vs 9.8 s); the neural agent wins on satellite-like and fast paths (`results/bbr_compare.txt`) |
| Fair between two neural flows with different RTTs? | `./eval_fair_rtt.sh 6` (real sockets) | Jain 0.84 at +150 ms RTT, against 0.64 for AIMD; still favours the short-RTT flow (`results/eval_fair_rtt_summary.txt`) |
| What does the network do? Can it be trusted? | `python3 tools/interpret.py`, `python3 tools/interp_eval.py` | Depth-5 tree agrees on 79% of decisions yet matches the transfer time on 4 of 5 paths; input noise 0.2 costs at most 12%; formal interval-bound certification only works at +-0.001 (43% of states) and fails at +-0.03 (`results/interp/`) |
| Does it starve kernel TCP? | `sudo bash netem_coexist.sh 5g\|lossy\|sat` (WSL or Linux with tc) | No: neural takes 24-34% of the link, AIMD 5-17% (3 runs per cell, `results/netem_coexist_summary.txt`) |

The BBR-style policy is a simplification (no ProbeRTT, no BBRv3 loss response), so no claim is made about the Linux BBR. Earlier versions of it stalled at 10% loss and took 163 s after a delay jump; both defects are fixed and described in the report (Section 8.19).

## REPLIC Drop: an application built on the library

`make drop` starts a small local web app (http://localhost:8800). Drop in any file (up to 12 MB), pick a network (5G, lossy Wi-Fi, satellite, long fast link, terrible link) and the policies to race (AIMD window 32, tuned AIMD, neural agent v1, REPLIC). Each transfer runs through the real C stack (libreplic sender, `bin/server`, `bin/impair` proxy), the receiver's copy is compared byte for byte (SHA-256), and the page shows time, goodput, retransmissions and the congestion window over time with retransmit and timer-expiry markers and the pipe-capacity line, with a download link for the verified copy. The page explains each phase (handshake, slow start, steady state, loss recovery, hash check), describes each policy and network, shows the best possible time on the chosen link, and writes a short per-run story from the sender's own trace. Policies run one after another, one run each, so small gaps are noise. Python is standard library only.

## Multi-machine run (two laptops, internet path)
One run, 5 reps, 2 MB each, over a Tailscale relay (~79 ms RTT, no direct connection): see `results/multihost_summary.txt` and `results/multihost_mylabel.txt` (transcribed from the client terminal). The path degraded mid-run (TCP 2 s to 51 s) and 4 of 20 REPLIC runs timed out, so this is a demonstration that REPLIC works between real machines, not a statistically valid comparison. On the clean reps kernel TCP was 2-6x faster than every REPLIC policy; in rep 1 the neural agent beat tuned AIMD (4.7 s vs 14.2 s). Repeat on a direct LAN path for stronger numbers.
