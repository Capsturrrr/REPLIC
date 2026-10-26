# Demo run-of-show (about 10 minutes)

Team: Harshavardhan Mallela, Rohit Arya, Shloak Sinha. Before starting: Packet Tracer open with the saved `.pkt`, a WSL terminal in the repo, the report PDF and slides open. Build once: `make all smart chat` (then `make test` if you want to show the green run).

## Order

1. **Idea (1 min), slides 2-3.** UDP is unreliable, TCP is locked in the kernel, so we rebuilt TCP's core ideas over UDP: handshake, Selective Repeat with a 256-packet window, CRC-16, adaptive timer, pluggable congestion control.
2. **REPLIC live (1 min).** Terminal 1 `./bin/server`, terminal 2 `./bin/client 20` (20% simulated loss). Point at TIMEOUT / FAST RETRANSMIT lines, then the reassembled message.
3. **Chat (2 min).** `./bin/chat 9001 9002 Alice 30` and `./bin/chat 9002 9001 Bob 30`. Start both terminals before typing. Messages arrive in order despite the "network lost" lines. Optional web version: `python3 web/replic_web.py --udp 9001 --peer 127.0.0.1:9002 --http 8080`.
4. **File transfer (1.5 min).** `./scripts/filetransfer_demo.sh 2 lossy` sends a 2 MB random file over an impaired path with three policies and checks that the received hash matches. Expected: about 27 s, 24 s and 12 s (AIMD cap 32, AIMD tuned, neural), all byte-identical.
5. **REPLIC (2 min).** Virtual time makes this fast and repeatable:
   `./bin/smart_client --vt 1 --mode deep --delay 25 --jitter 10 --loss 2 --rate 800 --queue 50 --packets 1200 --seed 1`
   then `python3 web/vt_named.py` (five named paths, 100 paired seeds). Show the headline table: neural beats tuned AIMD by 15% to 52% on four paths and ties on the 10%-loss path. Show `results/vt_ablation.txt` if asked what matters.
6. **Network (2 min).** Packet Tracer, four routers R1-R4, OSPF area 0. R2 `show ip ospf neighbor` (three neighbours FULL); PC `tracert 192.168.30.10`; ACL block (`ping 192.168.20.11` fails); failover (`ping -t`, shut R1 Gi0/2, tracert now goes via R2, restore with `no shutdown`). Measured: 70 of 71 pings survived the shutdown.
7. **Wireshark (1 min).** `wireshark -X lua_script:network/replic.lua network/smart_lossy_aimd.pcap`; filter `replic.flags.data == 1`; pick a seq with several copies to show a retransmission.

## Backup plan

* If a live demo misbehaves, `make test` output and the report's Section 8 hold the same evidence.
* If a port is busy: `pkill -x server` (never `pkill -f`).
* Windows cannot capture the WSL loopback: use `tshark -i lo` inside WSL, or open the supplied `.pcap` files.

## Honest limits (say them before you are asked)

* Packet Tracer cannot run the C programs, so the protocol and the network are validated separately.
* Results come from a software impairment proxy and a virtual-time build, not kernel netem: run `sudo bash scripts/netem_baselines.sh lossy|5g|sat|reorder` on WSL for that.
* On a shared bottleneck the neural agent takes about 68% of the link against tuned AIMD, so it is not a polite neighbour.
* No gain on the 10%-loss path. Earlier larger margins were inflated by a timer defect that hurt the baseline more; fixed and re-measured.
