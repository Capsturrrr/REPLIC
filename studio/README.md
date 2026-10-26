# REPLIC Studio

A browser dashboard around the real C sender, receiver and impairment proxy.

    pip install fastapi "uvicorn[standard]"
    python3 studio/app.py            # builds studio/bin on first run, serves http://127.0.0.1:8000

* Pick a path (loss, jitter, delay, bandwidth, queue, size) and up to 4 racers; each racer runs over its own identical
  emulated bottleneck on 127.0.0.1 (ports 9960+ and 9980+).
* Live every 100 ms over WebSocket `/ws`: cwnd, RTT, loss-event rate, goodput, FEC state, newest dissected packets.
* REST: `GET /api/config`, `POST /api/race`, `GET /api/race/{id}`, `POST /api/stop`, `GET /api/fairness`.

What is and is not real
* cwnd/RTT come from the sender's `--trace` file, goodput and the dissector from its `--pcap` file (`REPLIC_LIVE=1` makes the sender flush each line).
* "Loss rate" is the share of TIMEOUT/FAST_RETX events among trace events in the last second, not the link's configured loss.
* FEC is a fixed XOR parity packet per 8 data packets. It is not adaptive.
* One race is one sample; use `scripts/run_all_tests.py` and `scripts/run_fairness_test.py` for statistics.
* Local use only: no authentication, one race at a time.

## Coexistence arena (bandwidth-bully test)
`POST /api/coexist {subjects:[...], bg:"cubic"|"bbr", k:1..3, ...}` runs each selected policy on its own bottleneck shared with k background flows,
one policy after the other (concurrent groups starve the busy-polling senders of CPU on a 2-core machine and hide the effect), and reports Jain's index live and,
at the end, frozen at the moment the first flow finishes. The UI can repeat the test up to 5 times and shows mean ± sd.
Toggle policies: "Neural v1 (static DQN)", "Neural v1 + --polite guard" and "REPLIC (PPO, fairness-trained)" (needs `results/smart/smart_final.txt`).
In three back-to-back repeats on the development machine the static agent took 1.56 ± 0.15 of its fair share (Jain 0.86) and REPLIC 0.90 ± 0.05 (Jain 0.98) against two CUBIC flows.
