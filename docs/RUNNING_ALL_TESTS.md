# Running every test (current as of 12 Oct 2026)

All commands run from the repository root (`cd ~/replic`). Linux or WSL. Times are rough, on 2 CPU cores.

## 0. One-time setup
```
sudo apt update && sudo apt install -y build-essential python3 python3-numpy git
sudo apt install -y iproute2 iperf3          # only for the kernel (tc/netem) tests
git clone https://github.com/Capsturrrr/replic && cd replic
make all smart chat impair
```

## 1. Correctness (a minute or two)
| What | Command |
|---|---|
| Unit, end-to-end, handshake, file integrity, chat interop, simulator | `make test` |
| Packet parser under AddressSanitizer + UBSan | `make fuzz` |
| Library API | `bash tests/test_api.sh` |
| Several clients at once | `bash tests/test_multiclient.sh` |

## 2. Headline result in virtual time (seconds, deterministic)
| What | Command | Output |
|---|---|---|
| Headline table, 100 paired seeds (about 30 s to a few minutes) | `make repro` (or `bash repro.sh`) | `results/` + `figures/headline.png` |
| Five named paths | `python3 web/vt_named.py` | printed |
| 100 random held-out paths | `python3 web/vt_random.py 100` | printed |
| Ablation | `python3 web/vt_ablate.py` | printed |
| Virtual time vs real clock | `python3 web/vt_calib.py` | printed |
| Regenerate figures | `make figures` | `figures/` |

## 3. Real clock and real sockets (minutes each)
| What | Command | Output |
|---|---|---|
| Final comparison: original AIMD, tuned AIMD, tuned CUBIC, neural | `bash eval_final.sh` | `results/` |
| Same paths through the separate impairment proxy | `bash eval_proxy.sh` | `results/` |
| Two flows, fairness | `bash eval_fair.sh 5` | `results/` |
| Two flows, unequal RTT | `bash eval_fair_rtt.sh 6` | `results/eval_fair_rtt_summary.txt` |
| Bursty cross traffic | `bash eval_cross.sh 8` | `results/eval_cross_summary.txt` |
| Send a 2 MB file over an impaired path, verify identical bytes | `bash filetransfer_demo.sh 2 lossy` | printed |
| Raw sender speed on loopback | `bash bench_loopback.sh` | printed |

## 4. Research-gap tests (virtual time, paired seeds, 95% intervals)
| Question | Command | Output |
|---|---|---|
| Path changes mid-transfer | `python3 tools/dyn_eval.py 20 aimd,cubic,bbr,deep` | `results/dynamic_path.txt` |
| BBR-style baseline | `python3 tools/bbr_eval.py 20` | `results/bbr_compare.txt` |
| Robustness to input noise, distilled tree | `python3 tools/interp_eval.py` | `results/interp/robustness_and_distillation.txt` |
| Decision tree, interval-bound certification | `bash tools/make_statelogs.sh` then `python3 tools/interpret.py` | `results/interp/` |

Note: `interpret.py` overwrites `results/interp/*`. Re-generated logs give numbers close to, not identical with, the ones in the report (the report's numbers use a different log). To keep the report's files: `git checkout results/interp` afterwards.

## 5. Real Linux kernel (needs root and tc; WSL is fine)
```
sudo bash netem_full.sh lossy 5       # also: 5g, sat, reorder.  TCP, plain UDP, REPLIC (AIMD, CUBIC, neural), REPS runs each
sudo bash netem_coexist.sh 5g 3       # also: lossy, sat.  Kernel TCP and one REPLIC flow share the bottleneck
```
Each takes several minutes per path. If a run is interrupted: `sudo pkill -f tcp_bulk.py; sudo pkill server; sudo pkill smart_client; sudo tc qdisc del dev lo root`.
Results are in `results/netem_corrected.txt` and `results/netem_coexist_summary.txt`.

## 6. Two machines (see `docs/MULTIHOST.md`)
Server machine: `bash multihost_server.sh` (leave running). Client machine: `bash multihost_client.sh SERVER_IP 5 mylabel`.
Use the server's real address (Tailscale `100.x.x.x` or LAN IP, never a WSL `172.x`). Output: `results/multihost_mylabel.txt`, about 10 to 35 minutes.

## 7. Chat, Wireshark, network
| What | How |
|---|---|
| Chat | `make chat`, then see `docs/CHAT_ONLINE.txt` |
| Wireshark | Dissector `network/replic.lua`, steps in `network/WIRESHARK_STEPS.txt`, sample captures `network/*.pcap` |
| Packet Tracer OSPF network | build guide in your outputs folder (`Packet_Tracer_OSPF_Build_Guide.pdf`), router configs in `network/cfg`; save your file as `network/REPLIC_OSPF_Network.pkt` |
| Demo for the review | `docs/DEMO_RUNBOOK.md` |

## 8. Where the answers are written
The report (Section 8) and `README.md` list each result with the command that produced it. If a number here disagrees with a file in `results/`, the file in `results/` is the record.
