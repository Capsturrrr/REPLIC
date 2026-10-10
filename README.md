# RUDP: reliable UDP in C, with a learned congestion controller

[![ci](https://github.com/Capsturrrr/rudp/actions/workflows/ci.yml/badge.svg)](https://github.com/Capsturrrr/rudp/actions/workflows/ci.yml)

A reliable, congestion-controlled transport over UDP, written in C: handshake, Selective Repeat with a 256-packet window,
CRC-16, adaptive retransmission timer, pluggable congestion control (AIMD, CUBIC, tabular Q-learning, a small neural network),
file transfer, a chat application (terminal and web), a four-router OSPF network design, a Wireshark dissector, and the evaluation
tooling to test all of it honestly.

**Team:** Harshavardhan Mallela (24BAI0274), Rohit Arya (24BCE0819), Shloak Sinha (24BDS0378). Computer Networks, VIT Vellore, faculty Anuradha G.

## Headline results

Completion time of a transfer, neural agent against **tuned** baselines (AIMD with window cap 128, CUBIC with cap 128), same transport,
same timers, paired seeds, 95% intervals. Details and caveats in `docs/RUDP_Final_Project_Report.pdf`, Section 8.11 to 8.13.

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
* The server handles up to 16 clients at once (`tests/test_multiclient.sh`).
* Honest limits: on a shared bottleneck it takes 68% of the link against tuned AIMD (not a polite neighbour); no gain on the 10% loss path;
  no kernel netem or multi-machine test yet (`netem_baselines.sh` is provided). Earlier, larger margins were inflated by a timer defect that hurt
  the baseline more; it is fixed and every number above is re-measured.

## Quick start

```bash
make all smart chat            # build (gcc, make; Python 3 + numpy for the tools)
make test                      # unit, end-to-end, handshake, file integrity, chat interop, simulator
make fuzz                      # packet parser under AddressSanitizer + UBSan

./filetransfer_demo.sh 2 lossy # send a 2 MB random file over an impaired path with three policies, verify byte-identical
./bench_loopback.sh            # raw sender speed on loopback

# a transfer by hand
RUDP_OUT=/tmp/out.bin ./bin/server &                     # receiver writes the file, prints length and hash
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

Selective Repeat is the default; Go-Back-N is kept for comparison (`--gbn`, `RUDP_GBN=1`, `make server_gbn`). The original settings of the neural agent
(cap 32, no pacing, no slow start, no hand-over) are available with `RUDP_CLASSIC=1`.

## Chat

```bash
./bin/chat 9001 9002 Alice 30        # terminal chat: my_port peer_port name loss%
./bin/chat 9002 9001 Bob   30
python3 web/rudp_web.py --udp 9001 --peer 127.0.0.1:9002 --http 8080     # web gateway with live dashboard (see docs/CHAT_ONLINE.txt)
```
The gateway and the terminal chat speak the same protocol and are tested against each other at 20% loss in both directions.

## Network design and Wireshark

Four routers (R1 to R4), OSPF area 0, two VLANs, DHCP, an extended ACL, tested failover (70 of 71 pings during a link shutdown): configs in `network/cfg/`,
diagram `network/topology_final.png`. `network/rudp.lua` is a Wireshark dissector (`wireshark -X lua_script:network/rudp.lua`); see `network/WIRESHARK_STEPS.txt`.

## Status

Phases 0 to 7 (handshake, Go-Back-N, AIMD, netem, benchmarks), then Smart-RUDP, Selective Repeat, performance work, hardening and the real-socket and
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
git clone https://github.com/Capsturrrr/rudp.git ~/projects/rudp
cd ~/projects/rudp
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
