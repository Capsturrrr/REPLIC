# Implementation Guide: Custom Reliable Transport Protocol over UDP (Poise)

This guide breaks the project into phases so you can build incrementally, test
as you go, and always have something working to demo — even if you run out of
time before the final phase.

---

## Phase 0: Setup & Environment (Day 1)

- Pick your language. **Python (`socket` module)** is faster to prototype and
  fine for demonstrating concepts. **C** is more impressive for interviews if
  you have time, since raw socket + manual buffer management shows lower-level
  skill.
- Set up a Linux dev environment (native, WSL, or VM) — you'll need `tc`/`netem`
  later, which requires Linux.
- Initialize a git repo now. Interviewers will look at your commit history —
  frequent, well-described commits showing iterative design > one giant commit.

**Deliverable:** empty repo, README stub, environment confirmed working.

---

## Phase 1: Raw UDP Echo (Day 2–3)

Before adding reliability, get a basic UDP client-server exchanging messages.

- Implement a UDP server that listens on a port and echoes back received data.
- Implement a UDP client that sends a message and prints the echoed response.
- Confirm you understand UDP's unreliability firsthand: run it with `tc` packet
  loss enabled (e.g., 10%) and observe messages silently disappearing.

**Deliverable:** working UDP echo client/server. This is your baseline "before" demo.

---

## Phase 2: Packet Format & Sequencing (Day 4–6)

- Design your packet header. At minimum:
  ```
  [ seq_num | ack_num | flags (SYN/ACK/FIN/DATA) | checksum | payload_length | payload ]
  ```
- Implement serialization/deserialization of this header (e.g., `struct` in
  Python, or manual byte packing in C).
- Add a simple checksum for corruption detection (can be minimal — this isn't
  the focus).

**Deliverable:** you can pack/unpack custom headers reliably; write unit tests
for this early, since bugs here cascade into everything else.

---

## Phase 3: Connection Establishment (Day 7–8)

- Implement a simplified three-way handshake (SYN → SYN-ACK → ACK) to establish
  a "connection" (really just shared state: initial sequence numbers, window size).
- Implement connection teardown (FIN/ACK).
- Handle basic edge cases: duplicate SYNs, handshake timeout/retry.

**Deliverable:** client and server can establish and tear down a logical
connection over UDP.

---

## Phase 4: Reliable Delivery (Day 9–12) — Core of the Project

This is the heart of the project. Build it in this order:

1. **Stop-and-wait first.** Sender sends one packet, waits for ACK, retransmits
   on timeout. Get this rock solid before adding complexity — it's the
   easiest version to debug.
2. **Add sequence numbers + duplicate detection** on the receiver side, so
   retransmitted packets don't get processed twice.
3. **Upgrade to a sliding window.** Allow multiple unacknowledged packets
   in flight (fixed window size to start, e.g., 4 packets).
4. **Implement cumulative ACKs** and **fast retransmit** on triple-duplicate ACK
   (this is a classic TCP mechanism and a great talking point in interviews).
5. **Handle reordering and loss** explicitly — write test scenarios using
   `tc netem` to simulate reordering (`delay ... reorder`) and loss
   (`loss random X%`), and confirm your protocol recovers correctly.

**Deliverable:** reliable, in-order delivery under emulated loss/reordering —
demoable with a file transfer that completes correctly under 10–20% packet loss.

---

## Phase 5: Congestion Control (Day 13–16)

- Implement **AIMD (Additive Increase, Multiplicative Decrease)** — classic
  Reno-style congestion control:
  - Slow start (exponential window growth) until a threshold or loss event.
  - Congestion avoidance (linear growth) after threshold.
  - On loss: halve the window (multiplicative decrease).
- **Stretch goal:** implement a second algorithm (e.g., a simplified Cubic-style
  window growth function) so you can compare behaviors — this comparison is
  what makes the project stand out over a basic implementation.
- Log congestion window size over time to a file for later graphing.

**Deliverable:** congestion window that grows/shrinks in response to network
conditions, with logs proving it.

---

## Phase 6: Testing & Benchmarking (Day 17–19)

- Use `tc netem` to create multiple test profiles: low latency/no loss, high
  latency, high loss, jitter, bandwidth-constrained.
- Under each profile, measure and record:
  - Throughput (your protocol vs. native TCP vs. native UDP)
  - Average RTT
  - Retransmission count
  - Time to recover from a loss burst
- Use Wireshark to visually verify your handshake, retransmissions, and
  teardown look correct at the packet level — good screenshots for your report.

**Deliverable:** a spreadsheet or CSV of benchmark results across conditions
and protocols.

---

## Phase 7: Visualization & Report (Day 20–21)

- Plot congestion window size over time (classic "sawtooth" graph if AIMD is
  implemented correctly — this single graph is a great portfolio/interview visual).
- Plot throughput comparison (your protocol vs TCP vs UDP) across test conditions.
- Write up:
  - Design decisions and why you made them
  - Edge cases you hit and how you solved them
  - Where your implementation diverges from real TCP and why
  - Benchmark results and interpretation

**Deliverable:** final report + graphs, ready to attach to your resume/portfolio
and reference in interviews.

---

## Tips for Maximizing Interview Value

- **Keep a running log of bugs you hit and how you diagnosed them** (e.g., "ACK
  storm caused by X, fixed by Y"). These war stories are exactly what
  interviewers want to hear in "tell me about a challenging bug" questions.
- **Push incremental commits**, not one final dump — shows process, not just outcome.
  its.
- Write a clear README with a diagram of your packet format and state machine —
  this is often the first thing an interviewer skimming your GitHub will look at.
- If time is short, **Phases 0–4 alone are a legitimate, demoable project.**
  Phases 5–7 are what push it from "good" to "great."

## Suggested Timeline Summary

| Phase | Focus | Time |
|---|---|---|
| 0 | Setup | 1 day |
| 1 | UDP echo baseline | 2 days |
| 2 | Packet format | 3 days |
| 3 | Handshake | 2 days |
| 4 | Reliable delivery (core) | 4 days |
| 5 | Congestion control | 4 days |
| 6 | Testing/benchmarking | 3 days |
| 7 | Visualization/report | 2 days |

Total: ~3 weeks at a steady pace, compressible to ~10 days if you focus only
on Phases 0–4 plus a basic version of 5.
