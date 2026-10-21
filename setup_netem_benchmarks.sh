#!/usr/bin/env bash
# setup_netem_benchmarks.sh -- WSL2 / Linux helper for the kernel-netem and kernel-TCP benchmarks of REPLIC v2.
#
#   sudo bash setup_netem_benchmarks.sh check              # is tc/netem/iperf3 usable here? (changes nothing permanent)
#   sudo bash setup_netem_benchmarks.sh install            # apt install iproute2 iperf3 (needs network)
#   sudo bash setup_netem_benchmarks.sh list               # profiles from netem_profiles.tsv
#   sudo bash setup_netem_benchmarks.sh apply loss10_j35   # install that profile on lo (both directions) until you run 'clear'
#   sudo bash setup_netem_benchmarks.sh clear              # remove netem/tbf and restore lo's MTU
#   sudo bash setup_netem_benchmarks.sh tcp loss10_j35 [BYTES] [REPS]   # kernel TCP only, one profile, prints CSV rows (policy=tcp)
#   sudo bash setup_netem_benchmarks.sh all [--quick]      # the whole matrix incl. kernel TCP via run_all_tests.py --backend netem
#
# Needs root and a kernel with sch_netem. On WSL2 the stock Microsoft kernel has it; if 'check' says "netem not usable" see the end of this file.
# Everything is applied to the loopback device and reverted by 'clear' or on exit from 'tcp'/'all'. No other interface is touched.
# Profiles are read from netem_profiles.tsv, the same file run_all_tests.py uses (single source of truth).
# STATUS: written without access to tc in the authoring sandbox, so it is verified only with 'bash -n' and the dry-run printing of the
# commands (python3 run_all_tests.py --netem-dry-run); the first real run is on your machine. Run 'check' first.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
PROFILES="$HERE/netem_profiles.tsv"; PORT=9971
need_root() { [ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }; }
mtu_file=/tmp/replic_lo_mtu.orig

get_profile() {   # sets NAME DELAY JITTER LOSS RATE QUEUE NOTE
  local line; line=$(awk -F'\t' -v n="$1" 'NR>1 && $1==n {print}' "$PROFILES")
  [ -n "$line" ] || { echo "unknown profile '$1' (try: list)"; exit 1; }
  IFS=$'\t' read -r NAME DELAY JITTER LOSS RATE QUEUE NOTE <<<"$line"
}

apply_profile() {
  get_profile "$1"; need_root
  [ -f $mtu_file ] || cat /sys/class/net/lo/mtu > $mtu_file
  ip link set dev lo mtu 1500 || { echo "cannot set lo MTU"; exit 1; }
  tc qdisc del dev lo root 2>/dev/null
  local jit="" dist="distribution normal"
  awk "BEGIN{exit !($JITTER>0)}" && jit="${JITTER}ms"
  # try Gaussian jitter first; fall back to uniform if the iproute2 'normal' table is not installed
  if [ -n "$jit" ] && tc qdisc add dev lo root handle 1: netem delay ${DELAY}ms $jit $dist loss ${LOSS}% limit 100000 2>/dev/null; then :
  else
    [ -n "$jit" ] && echo "note: no normal.dist table, using uniform jitter"
    tc qdisc del dev lo root 2>/dev/null
    tc qdisc add dev lo root handle 1: netem delay ${DELAY}ms ${jit} loss ${LOSS}% limit 100000 || { echo "netem failed (sch_netem missing?)"; exit 1; }
  fi
  local kbit; kbit=$(awk "BEGIN{printf \"%d\", $RATE*8}"); local lim; lim=$(awk "BEGIN{printf \"%d\", $QUEUE*1000+16000}")
  tc qdisc add dev lo parent 1:1 handle 10: tbf rate ${kbit}kbit burst 16kb limit $lim || { echo "tbf failed"; exit 1; }
  echo "applied '$NAME' on lo: one-way delay ${DELAY}ms jitter ${JITTER}ms loss ${LOSS}% (each direction), bottleneck ${RATE} kB/s, queue ${QUEUE} kB  [$NOTE]"
}

clear_all() {
  tc qdisc del dev lo root 2>/dev/null
  if [ -f $mtu_file ]; then ip link set dev lo mtu "$(cat $mtu_file)" 2>/dev/null; rm -f $mtu_file; fi
  echo "cleared netem/tbf on lo and restored its MTU"
}

check() {
  local ok=1
  for t in tc ip python3; do command -v $t >/dev/null && echo "ok   $t" || { echo "MISSING $t"; ok=0; }; done
  command -v iperf3 >/dev/null && echo "ok   iperf3 (optional)" || echo "note iperf3 not installed (optional; the TCP collector uses python sockets)"
  [ "$(id -u)" = 0 ] && echo "ok   running as root" || { echo "MISSING root (use sudo)"; ok=0; }
  if [ $ok = 1 ]; then
    tc qdisc del dev lo root 2>/dev/null
    if tc qdisc add dev lo root netem delay 1ms 1ms distribution normal 2>/dev/null; then echo "ok   netem with Gaussian jitter"
    elif tc qdisc add dev lo root netem delay 1ms 2>/dev/null; then echo "ok   netem (jitter will be UNIFORM: normal.dist table missing)"
    else echo "FAIL netem not usable: kernel lacks sch_netem (see notes at the end of this file)"; ok=0; fi
    tc qdisc del dev lo root 2>/dev/null
  fi
  [ $ok = 1 ] && echo "READY" || { echo "NOT READY"; return 1; }
}

tcp_only() {
  local prof=$1 bytes=${2:-1000000} reps=${3:-5}; apply_profile "$prof"; trap clear_all EXIT
  echo "scenario,policy,size,rep,ok,fail,ms,goodput_MBps,sent,retx,timeouts"
  python3 - "$prof" "$bytes" "$reps" <<'PY'
import sys; sys.path.insert(0, ".")
import run_all_tests as R
prof, size, reps = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
sc = [m for m in R.MATRIX if m["name"] == prof][0]
for i in range(reps):
    r = R.run_tcp(sc, size, i)
    print(",".join(str(r[k]) for k in ("scenario","policy","size","rep","ok","fail","ms","goodput_MBps","sent","retx","timeouts")))
PY
}

case "${1:-}" in
  check)   check ;;
  install) need_root; apt-get update -y && apt-get install -y iproute2 iperf3 python3-matplotlib python3-scipy ;;
  list)    awk -F'\t' 'NR>1 {printf "%-12s delay %4sms jitter %3sms loss %5s%% rate %5s kB/s queue %3s kB  %s\n",$1,$2,$3,$4,$5,$6,$7}' "$PROFILES" ;;
  apply)   apply_profile "${2:?profile name}" ;;
  clear)   need_root; clear_all ;;
  tcp)     need_root; tcp_only "${2:?profile name}" "${3:-}" "${4:-}" ;;
  all)     need_root; shift; python3 run_all_tests.py --backend netem "$@" ;;
  *)       sed -n '2,13p' "$0"; exit 1 ;;
esac

# Notes
#  * netem on lo impairs BOTH directions, so a 25 ms profile has a 50 ms RTT and ACKs can be lost; the proxy backend impairs forward packets only.
#    Do not mix the two backends in one comparison table without saying so (run_all_tests.py writes the backend into tables.tex and BACKEND.txt).
#  * WSL2 without sch_netem: build a kernel with CONFIG_NET_SCH_NETEM=m (and CONFIG_NET_SCH_TBF=m), or run this on a normal Linux VM / cloud instance.
#  * Kernel TCP retransmissions come from the system-wide Tcp RetransSegs counter: close other network-heavy programs while the benchmark runs.
#  * iperf3 is optional. Its numbers are not used because the collector times the same bytes and the same end condition as REPLIC.
