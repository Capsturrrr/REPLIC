# Poise v2: blueprint, status, and the Poise-Vault specification

Status key: **BUILT** (code exists and was run), **FORMULATED** (math written, not implemented), **SPEC** (design only).

## 1. What v2 contains

| Item | Status | Where |
|---|---|---|
| 1,200-byte payloads (BUFFER_SIZE 2048) | BUILT, measured | `src/common.h` |
| XOR forward error correction, group size `POISE_FEC=K` (sender), always-on receiver recovery | BUILT, measured | `src/poise_sender.c` (TX macro), `src/server.c` (`fec_try`) |
| FIN retransmission fix (found by the orchestrator) | BUILT, also applied to v1 | `src/poise_sender.c` |
| One-command test orchestrator | BUILT, proxy backend run; netem backend NOT run | `run_all_tests.py` |
| Time-aligned multi-panel telemetry figure (cwnd, RTT/SRTT/RTO bound, loss events, goodput), PDF+SVG | BUILT, from client traces | `run_all_tests.py: telemetry_figure` |
| LaTeX tables and completion-time figure from raw logs | BUILT | `results/orch/tables.tex`, `fig_completion.pdf` |
| Paper sections III, IV, V | DRAFTED, compiles; extended MDP and Lyapunov parts labelled as not implemented | `paper/sections_v2.tex` |
| Extended MDP (BDP estimate, RTT gradient, queue derivative, continuous action) | FORMULATED only | paper section III-B |
| Safety shield (window <= beta*BDP + Q) | FORMULATED; the proposition is trivial by construction; no Lyapunov guarantee is claimed | paper section III-C |
| Reed-Solomon FEC, adaptive K | NOT implemented | |
| Jain fairness against CUBIC/BBR competitors, convergence time | NOT measured in v2 | |
| 1 GB transfers | NOT supported (sender limit ~78 MB per call) | |
| Kernel-bypass / zero-copy | NOT implemented (see 3) | |
| Poise-Vault daemon and ncurses TUI | SPEC only (below) | |

## 2. How to run the matrix

    python3 run_all_tests.py --quick                     # ~3 min: 3 scenarios x 7 policies x 2 reps, 256 kB
    python3 run_all_tests.py --scenarios loss10_j35 --policies aimd,aimd+fec --reps 20
    python3 run_all_tests.py                             # full matrix, 5 reps, 100 kB / 1 MB / 8 MB (long)

Outputs go to `results/orch/`: `raw.csv`, `summary.csv` (mean, median, p95, p99), `tables.tex`, `fig_completion.*`, `telemetry_*.pdf/svg`, per-run traces and, on any integrity failure, the server log.
Every run's delivered file is compared byte for byte; a failure is recorded, never dropped.

## 3. Why "kernel bypass" is not claimed
Poise already runs entirely in user space over an ordinary UDP socket, but that is not kernel bypass: every packet still crosses the kernel's UDP path through one `sendto`/`recvfrom` system call. Real bypass means AF_XDP, DPDK or io_uring with registered buffers. The cheap, honest step that is compatible with this code base is batching with `sendmmsg`/`recvmmsg` plus a preallocated packet ring; it reduces system calls per packet but does not remove the kernel from the data path. Not implemented; the measured v2 speed-ups come from packet size and FEC only.

## 4. Poise-Vault specification (SPEC only)

Goal: peer-to-peer encrypted file sync plus a low-latency telemetry stream over one Poise association.

**Transport framing** (inside Poise payload, after the 13-byte Poise header):

    stream_id   u16   0 = control, 1 = telemetry, 2.. = file streams
    frame_type  u8    OPEN, DATA, FIN, WINDOW_UPDATE, KEY_UPDATE
    flags       u8    bit0 = fec-protected, bit1 = priority
    offset      u64   byte offset within the stream (DATA)
    length      u16
    nonce       u96   unique per frame (counter-based)
    ciphertext  ...   AEAD-sealed (see below)
    tag         u128

**Multiplexing.** One Poise connection carries all streams; the sender schedules frames by priority (telemetry before bulk) with a small per-stream credit. Head-of-line blocking remains at the Poise level because Poise delivers one ordered byte stream; per-stream ordering above it does not remove that. True independence needs per-stream sequence spaces, which is a protocol change not made here.

**Encryption.** Use an existing AEAD and key exchange (libsodium: X25519 key agreement, ChaCha20-Poly1305), never hand-rolled primitives. Peer keys are authenticated out of band (fingerprint comparison) or with a pre-shared key. No keys, tokens or secrets live in the repository.

**Adaptive FEC.** Choose K from the observed loss rate over a sliding window: no FEC below ~1% loss (measured as pure overhead on clean paths), K=8 around 5-10%, smaller K above. The thresholds are a hypothesis to be set from the measurements in `results/orch`, not established values.

**TUI.** ncurses panels fed by the existing trace events: cwnd and RTT sparklines, goodput, loss and FEC-recovery counters, per-stream progress bars. Source of truth is the same CSV trace the plots use.

**Why it is not implemented here.** A correct encrypted multi-stream daemon with a TUI, tested over loss, is several weeks of work; shipping untested crypto would be worse than shipping none. The existing pieces it would reuse (Poise sender/receiver, FEC, trace export) are built and measured.
