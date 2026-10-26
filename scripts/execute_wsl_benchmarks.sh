#!/usr/bin/env bash
# scripts/execute_wsl_benchmarks.sh -- run the REPLIC / REPLIC kernel-netem + native kernel-TCP (CUBIC, BBR) benchmarks on WSL2 or any Linux.
#
#   sudo bash scripts/execute_wsl_benchmarks.sh --check            # preflight only, changes nothing permanent
#   sudo bash scripts/execute_wsl_benchmarks.sh --quick            # ~10 min smoke run: 3 profiles, 2 reps, 100 kB + scale matrix with n=3
#   sudo bash scripts/execute_wsl_benchmarks.sh                    # default: matrix (10 profiles, 10 reps, 1 MB) + scale matrix (3 path families, n=30)
#   sudo bash scripts/execute_wsl_benchmarks.sh --reps 30 --n 30 --with-fairness     # everything, long (hours)
#
# Options
#   --check              preflight only
#   --quick              small smoke configuration (see above)
#   --reps N             repetitions per cell of the profile matrix            (default 10)
#   --sizes LIST         comma list of transfer sizes in bytes for the matrix   (default 1000000)
#   --n N                seeds per cell of the scale matrix                     (default 30)
#   --skip-matrix        do not run the 10-profile matrix
#   --skip-scale         do not run the multi-path scale matrix
#   --with-fairness      also run scripts/run_fairness_test.py --backend netem (kernel TCP background flows; untested before your run)
#   --fairness-reps N    repetitions per fairness cell                          (default 30)
#   --out DIR            output directory                                       (default results/wsl_netem)
#   --force              continue even if lo already has a non-default qdisc (it will be REPLACED and then removed)
#   -h, --help
#
# What it touches: ONLY the loopback device `lo` (root qdisc: netem + tbf, MTU 1500, offloads off) plus the sysctl net.ipv4.tcp_no_metrics_save.
# Every change is reverted by the EXIT/INT/TERM/HUP trap, also if you press Ctrl-C. No other interface and no routing table is modified.
# Why `lo` and not eth0: both endpoints run on this machine, so their packets only ever cross lo; shaping eth0 would not affect them.
#
# Kernel TCP uses the same payload bytes (random, sha256-checked), the same netem+tbf bottleneck, the same timer start (after connect/handshake)
# and the same end condition (receiver has every byte and answers) as the REPLIC client; see scripts/run_all_tests.py:run_tcp.
#
# STATUS: written in a sandbox without tc/ip. Verified there with `bash -n` and with a stub-`tc` dry run of the control flow (see tests/test_wsl_script.sh);
# the first real kernel run is yours -- start with --check, then --quick.
set -u -o pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$HERE"

REPS=10; SIZES=1000000; NSEEDS=30; OUT="results/wsl_netem"; QUICK=0; CHECK=0; SKIP_MATRIX=0; SKIP_SCALE=0; FAIR=0; FREPS=30; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;; --quick) QUICK=1 ;; --reps) REPS="$2"; shift ;; --sizes) SIZES="$2"; shift ;; --n) NSEEDS="$2"; shift ;;
    --skip-matrix) SKIP_MATRIX=1 ;; --skip-scale) SKIP_SCALE=1 ;; --with-fairness) FAIR=1 ;; --fairness-reps) FREPS="$2"; shift ;;
    --out) OUT="$2"; shift ;; --force) FORCE=1 ;; -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (try --help)"; exit 64 ;;
  esac; shift
done
if [ "$QUICK" = 1 ]; then REPS=2; SIZES=100000; NSEEDS=3; FREPS=3; fi

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARNING:\033[0m %s\n' "$*"; }
die()  { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit "${2:-1}"; }
PYTHON="${PYTHON:-python3}"

# ------------------------------------------------------------------------------------------------ preflight
PROBLEMS=()
preflight() {
  say "Preflight"
  [ "$(uname -s)" = Linux ] || PROBLEMS+=("not Linux ($(uname -s)); use WSL2 (Ubuntu) or a Linux VM")
  grep -qi microsoft /proc/version 2>/dev/null && echo "environment: WSL ($(uname -r))" || echo "environment: native Linux ($(uname -r))"
  [ "$(id -u)" = 0 ] || PROBLEMS+=("not root: run  sudo bash $0 $*")
  for t in tc ip gcc "$PYTHON"; do command -v "$t" >/dev/null 2>&1 && echo "ok   $t" || PROBLEMS+=("missing '$t'")
  done
  "$PYTHON" - <<'PY' 2>/dev/null || PROBLEMS+=("python modules numpy/scipy/matplotlib missing (sudo apt install python3-numpy python3-scipy python3-matplotlib)")
import numpy, scipy, matplotlib
PY
  command -v ethtool >/dev/null 2>&1 && echo "ok   ethtool (used to turn off GSO/TSO/GRO on lo)" || warn "ethtool missing: netem will see 64 kB GSO super-packets, which makes per-packet loss inaccurate. sudo apt install ethtool"
  if [ "${#PROBLEMS[@]}" -eq 0 ]; then
    modprobe sch_netem 2>/dev/null; modprobe sch_tbf 2>/dev/null; modprobe tcp_bbr 2>/dev/null
    existing="$(tc qdisc show dev lo 2>/dev/null | grep -E '^qdisc (netem|tbf|htb|prio|pfifo_fast|fq|mq|cake)' || true)"
    if [ -n "$existing" ] && [ "$FORCE" != 1 ]; then PROBLEMS+=("lo already has a custom qdisc: $existing   (re-run with --force to replace it, it will be removed at exit)"); fi
    tc qdisc del dev lo root 2>/dev/null
    if tc qdisc add dev lo root netem delay 1ms 1ms distribution normal 2>/dev/null; then echo "ok   netem with Gaussian jitter"
    elif tc qdisc add dev lo root netem delay 1ms 2>/dev/null; then warn "netem works but without the 'normal' table: jitter will be UNIFORM (apt install iproute2 provides /usr/lib/tc/normal.dist)"
    else PROBLEMS+=("netem not usable on lo: kernel lacks sch_netem (WSL2: use a kernel built with CONFIG_NET_SCH_NETEM=m and CONFIG_NET_SCH_TBF=m, or run on an Ubuntu VM / cloud instance)"); fi
    tc qdisc del dev lo root 2>/dev/null
    if tc qdisc add dev lo root handle 1: netem delay 1ms 2>/dev/null && tc qdisc add dev lo parent 1:1 handle 10: tbf rate 10mbit burst 16kb limit 32kb 2>/dev/null; then echo "ok   netem + tbf stack"
    else PROBLEMS+=("tbf could not be attached below netem (sch_tbf missing?)"); fi
    tc qdisc del dev lo root 2>/dev/null
    cc="$(cat /proc/sys/net/ipv4/tcp_available_congestion_control 2>/dev/null)"; echo "kernel TCP congestion controls: ${cc:-unknown}"
    case " $cc " in *" cubic "*) ;; *) warn "kernel has no cubic: tcp_cubic rows will be skipped" ;; esac
    case " $cc " in *" bbr "*) echo "ok   tcp_bbr available" ;; *) warn "tcp_bbr not available (sudo modprobe tcp_bbr failed): tcp_bbr rows will be skipped" ;; esac
  fi
  if [ "${#PROBLEMS[@]}" -gt 0 ]; then
    printf '\n\033[31mNOT READY\033[0m -- fix the following and re-run:\n'; for p in "${PROBLEMS[@]}"; do echo "  - $p"; done
    cat <<'TXT'

Typical fix on Ubuntu / WSL2:
  sudo apt update && sudo apt install -y iproute2 gcc python3 python3-numpy python3-scipy python3-matplotlib ethtool
  sudo modprobe sch_netem sch_tbf tcp_bbr
If `modprobe sch_netem` fails on WSL2 the stock kernel was built without it. Either install a newer WSL kernel (wsl --update, from Windows) or
use an Ubuntu VM. Nothing in this script can work around a missing sch_netem, and it will not pretend otherwise.
TXT
    return 2
  fi
  echo "READY"; return 0
}

preflight "$@" || exit $?
[ "$CHECK" = 1 ] && exit 0

# ------------------------------------------------------------------------------------------------ state + guaranteed cleanup
mkdir -p "$OUT"; LOG="$OUT/run.log"; exec > >(tee -a "$LOG") 2>&1
ORIG_MTU="$(cat /sys/class/net/lo/mtu 2>/dev/null || echo 65536)"
ORIG_NOMETRICS="$(cat /proc/sys/net/ipv4/tcp_no_metrics_save 2>/dev/null || echo 0)"
ORIG_OFFLOAD=""
if command -v ethtool >/dev/null 2>&1; then ORIG_OFFLOAD="$(ethtool -k lo 2>/dev/null | awk -F': ' '/^(tcp-segmentation-offload|generic-segmentation-offload|generic-receive-offload):/ {split($2,a," "); print $1"="a[1]}' | tr '\n' ' ')"; fi
CHILD=0; CLEANED=0
cleanup() {
  [ "$CLEANED" = 1 ] && return; CLEANED=1
  trap '' INT TERM HUP
  echo; echo "== cleanup: removing netem/tbf from lo and restoring settings"
  [ "$CHILD" != 0 ] && kill "$CHILD" 2>/dev/null && wait "$CHILD" 2>/dev/null
  pkill -x server_drop 2>/dev/null; pkill -x impair 2>/dev/null; pkill -x smart_client 2>/dev/null
  tc qdisc del dev lo root 2>/dev/null
  ip link set dev lo mtu "$ORIG_MTU" 2>/dev/null
  echo "$ORIG_NOMETRICS" > /proc/sys/net/ipv4/tcp_no_metrics_save 2>/dev/null
  if [ -n "$ORIG_OFFLOAD" ] && command -v ethtool >/dev/null 2>&1; then
    for kv in $ORIG_OFFLOAD; do case "$kv" in tcp-segmentation-offload=*) f=tso;; generic-segmentation-offload=*) f=gso;; generic-receive-offload=*) f=gro;; *) continue;; esac
      ethtool -K lo "$f" "${kv#*=}" >/dev/null 2>&1; done
  fi
  if tc qdisc show dev lo 2>/dev/null | grep -qE 'netem|tbf'; then warn "lo STILL has netem/tbf; remove with: sudo tc qdisc del dev lo root"; else echo "lo is clean (mtu $ORIG_MTU)"; fi
}
trap cleanup EXIT
trap 'echo "interrupted"; cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM HUP

[ "$FORCE" = 1 ] && tc qdisc del dev lo root 2>/dev/null
echo 1 > /proc/sys/net/ipv4/tcp_no_metrics_save 2>/dev/null || warn "could not set tcp_no_metrics_save: kernel TCP may reuse cached ssthresh between runs"
if command -v ethtool >/dev/null 2>&1; then ethtool -K lo tso off gso off gro off >/dev/null 2>&1 || warn "ethtool could not disable offloads on lo"; fi
LOAD="$(cut -d' ' -f1 /proc/loadavg)"; "$PYTHON" -c "import sys; sys.exit(0 if float('$LOAD') < 0.5 else 1)" || warn "load average is $LOAD: close other programs, kernel-TCP retransmission counts come from a system-wide counter"

# the environment record that goes into the results tarball
{
  echo "date: $(date -Is)"; echo "host: $(hostname)"; echo "kernel: $(uname -a)"; echo "tc: $(tc -V 2>&1)"
  echo "tcp congestion controls: $(cat /proc/sys/net/ipv4/tcp_available_congestion_control)"; echo "default cc: $(cat /proc/sys/net/ipv4/tcp_congestion_control)"
  echo "lo original mtu: $ORIG_MTU"; echo "lo offloads before: $ORIG_OFFLOAD"; echo "python: $($PYTHON -V 2>&1)"; echo "gcc: $(gcc --version | head -1)"
  echo "git: $(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo none)"; echo "args: reps=$REPS sizes=$SIZES n=$NSEEDS quick=$QUICK fairness=$FAIR"
  echo "nproc: $(nproc)"; echo "cpu: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2)"
} > "$OUT/environment.txt"

# run a step in the background so that a trapped signal is handled at once
step() {  # step <title> <command...>
  local title="$1"; shift; say "$title"; echo "+ $*"
  "$@" & CHILD=$!; wait "$CHILD"; local rc=$?; CHILD=0
  [ $rc -eq 0 ] && echo "-- $title: ok" || warn "$title exited with status $rc"
  return $rc
}

KPOL="tcp_cubic,tcp_bbr,aimd,cubic,bbr,neural,smart"
STATUS=0

# ------------------------------------------------------------------------------------------------ 1. profile matrix (clean, loss 1-20 %, jitter, WAN, LTE, satellite)
if [ "$SKIP_MATRIX" != 1 ]; then
  SC=""; [ "$QUICK" = 1 ] && SC="--scenarios clean,loss2_j10,loss10_j35"
  step "Profile matrix on lo (netem + tbf): kernel TCP CUBIC/BBR vs REPLIC policies, $REPS reps, sizes $SIZES" \
    "$PYTHON" scripts/run_all_tests.py --backend netem --out "$OUT/matrix" --reps "$REPS" --sizes "$SIZES" --policies "$KPOL" $SC || STATUS=1
fi

# ------------------------------------------------------------------------------------------------ 2. multi-path, multi-seed scale matrix
if [ "$SKIP_SCALE" != 1 ]; then
  step "Scale matrix on lo (netem + tbf): 3 path families x $NSEEDS seeds, kernel TCP + REPLIC" \
    "$PYTHON" scripts/run_scale_matrix.py --backend netem --n "$NSEEDS" --out "$OUT/scale" || STATUS=1
fi

# ------------------------------------------------------------------------------------------------ 3. optional: coexistence against kernel-TCP background flows
if [ "$FAIR" = 1 ]; then
  step "Fairness vs kernel TCP background flows (cubic, bbr), $FREPS reps" \
    "$PYTHON" scripts/run_fairness_test.py --backend netem --out "$OUT/fairness" --reps "$FREPS" --subjects neural,neural_polite,smart_fair --background cubic,bbr --nbg 1,2 || STATUS=1
fi

# ------------------------------------------------------------------------------------------------ 4. harmonise + package
say "Package results"
GA=(--out-dir "$OUT/tex"); [ -f "$OUT/scale/raw.csv" ] && GA+=(--netem-scale "$OUT/scale/raw.csv"); [ -f "$OUT/matrix/raw.csv" ] && GA+=(--netem-matrix "$OUT/matrix/raw.csv")
"$PYTHON" scripts/gen_paper_tables.py "${GA[@]}" || STATUS=1
[ -n "${SUDO_UID:-}" ] && chown -R "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$OUT" 2>/dev/null
TAR="$(dirname "$OUT")/$(basename "$OUT")_$(hostname)_$(date +%Y%m%d_%H%M).tar.gz"
tar czf "$TAR" -C "$(dirname "$OUT")" "$(basename "$OUT")" --exclude='work' --exclude='work_dir' --exclude='traces' 2>/dev/null && echo "bundle: $TAR"
[ -n "${SUDO_UID:-}" ] && chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$TAR" 2>/dev/null
cat <<TXT

Next
  1. Copy $TAR back to the project (WSL: it is already visible from Windows under \\\\wsl\$\\...).
  2. tar xzf it in the project root, then:  python3 scripts/gen_paper_tables.py --netem-scale $OUT/scale/raw.csv --netem-matrix $OUT/matrix/raw.csv   (already run once inside $OUT/tex)
  3. \input{} the generated results/paper_tex/*.tex files in the paper (see paper/section_V_scale_insert.tex).
Exit status: $STATUS (non-zero means at least one step failed -- read $LOG before using any numbers)
TXT
exit $STATUS
