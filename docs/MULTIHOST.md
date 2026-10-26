# Multi-machine test (two real computers over a real network)

Goal: the same comparison as the kernel netem test, but over a real path (Wi-Fi, mobile hotspot, the internet through Tailscale, or a cloud VM).
The client sends 2 MB (4000 x 512 B) with kernel TCP and with REPLIC (tuned AIMD, CUBIC, BBR-style, neural), alternating, several times.

## Pick two machines
Any two of: your laptop, Rohit's or Shloak's laptop, a phone hotspot + a laptop, a free cloud VM. The more different the path, the more interesting
(same Wi-Fi = boring and very fast; laptop on mobile hotspot to a friend's laptop on home broadband = good; Tailscale across cities = good).
Both need Linux or WSL with `git`, `gcc`, `make`, `python3`. If they are not on the same network, install Tailscale on both (free) and use the 100.x.y.z address.

## On the SERVER machine
    git clone https://github.com/Capsturrrr/replic && cd replic      # or: git pull
    bash scripts/multihost_server.sh
Leave it running. If there is a firewall, allow UDP 8888 and TCP 9871.
(WSL2 note: WSL is behind NAT, so a WSL server is not reachable from outside. Use Tailscale inside WSL, or make the Windows machine the client and a Linux box or VM the server.)

## On the CLIENT machine
    git clone https://github.com/Capsturrrr/replic && cd replic      # or: git pull
    bash scripts/multihost_client.sh SERVER_IP 5 mylabel
SERVER_IP is the server's address (Tailscale 100.x.y.z, public IP of the VM, or LAN IP). `5` is the number of repetitions, `mylabel` names the output,
for example `hotspot_to_homewifi`. It takes about a minute per repetition on a decent path, longer on a slow one.
Send back `results/multihost_mylabel.txt` (or a screenshot). Note in one line what the two machines and links were (for example "laptop on Jio hotspot to Rohit's laptop on ACT broadband via Tailscale").

## Do two or three different paths if you can
Each file is one path. Name them differently. No `sudo` is needed on either machine.

## What we do with it
It becomes a new table in the report ("real multi-machine path"), replaces the "no multi-machine test" limitation, and strengthens the paper.
