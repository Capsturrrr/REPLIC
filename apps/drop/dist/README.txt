Poise DROP  -  send a file over our reliable-UDP protocol across a simulated bad network and watch the congestion-control
policies race (AIMD vs the neural agent). The receiver checks every byte.

WHAT YOU NEED
  Linux, macOS, or Windows with WSL (Ubuntu), plus python3 and gcc.  (Ubuntu/WSL: sudo apt install python3 build-essential)

RUN IT
  unzip poise-drop.zip && cd poise-drop
  ./start.sh
  then open http://localhost:8800   (it also tries to open your browser)

USE IT
  1. Drop in any file (up to 12 MB; 1 MB takes about 30 s on the lossy network).
  2. Pick a network: 5G, lossy Wi-Fi, satellite, long fast link or terrible link.
  3. Pick the policies to race and press "Send it".
  Transfers run one after another. Each shows live progress, time, goodput, retransmissions, the congestion window over time,
  and a green tick when the received copy is byte-identical (SHA-256). You can download the verified copy.

NOTES
  * Everything runs on your own machine, over loopback. The "network" is a C proxy (bin/impair) that adds delay, jitter, random
    loss and a drop-tail bottleneck. Nothing is sent over the internet.
  * One run per policy, so small differences are noise. Kernel TCP is not in this race (it cannot run through a UDP proxy).
  * Only one transfer at a time. Do not expose the port to the internet: it accepts file uploads.
  * Docker instead:  docker build -t poise-drop . && docker run --rm -p 8800:8800 poise-drop
  * Source and report: github.com/Capsturrrr/poise   (MIT licence)
  * Different port: PORT=8801 ./start.sh
