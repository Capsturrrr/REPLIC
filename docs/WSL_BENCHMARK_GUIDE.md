# Running the kernel-netem and kernel-TCP benchmark on WSL2 (or any Linux)

Run these inside the `poise2` folder (from `poise_v2_sandbox.zip`). Everything touches only the loopback device and is undone by `clear`.

    sudo bash setup_netem_benchmarks.sh install     # iproute2, iperf3 (optional), matplotlib, scipy
    sudo bash setup_netem_benchmarks.sh check       # must end with READY
    sudo bash setup_netem_benchmarks.sh list        # the profiles (same file run_all_tests.py reads)

Quick end-to-end test (about 5 minutes, includes kernel TCP):

    sudo python3 run_all_tests.py --backend netem --quick

Full matrix (long): `sudo bash setup_netem_benchmarks.sh all`.
Kernel TCP alone on one profile, as CSV rows: `sudo bash setup_netem_benchmarks.sh tcp loss10_j35 1000000 10 > tcp_rows.csv`.

If `tc` or root is missing, `run_all_tests.py --backend netem` falls back to the UDP proxy by itself, says so on screen, and writes the actual backend into `results/orch/BACKEND.txt` and the header of `tables.tex`. Kernel TCP is only run when real netem is active.

Outputs in `results/orch/`: `raw.csv`, `summary.csv` (mean, median, p95, p99), `tables.tex`, `fig_completion.pdf/svg`, `telemetry_*.pdf/svg`, `significance.txt` (Welch tests for each FEC pair and each policy against kernel TCP).
Merging runs from two machines: `python3 run_all_tests.py --merge a/raw.csv b/raw.csv`.

Things to know before reading the numbers
- netem on loopback delays and drops both directions: a 25 ms profile has a 50 ms RTT and ACKs can be lost. The proxy impairs forward packets only. Do not put proxy and netem rows in one table without saying so.
- The first real netem run is the first time this code touches real `tc` (it was written without `tc`). The generated commands match the dry run (`python3 run_all_tests.py --netem-dry-run`) and the shell script's stubbed test, but expect to fix small things.
- Kernel TCP retransmissions come from the system-wide `Tcp RetransSegs` counter: close other network-heavy programs.
- Sender limit is about 78 MB per transfer; 1 GB is rejected.
- If your WSL kernel has no `sch_netem`, `check` says "netem not usable"; build the kernel with CONFIG_NET_SCH_NETEM=m and CONFIG_NET_SCH_TBF=m, or use a plain Linux VM.
