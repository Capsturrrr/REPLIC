#!/usr/bin/env python3
"""
Poise Phase 7 — plot congestion window over time from logs/cwnd_log.csv
Usage: python3 plot_cwnd.py [csv_path] [output_png]
"""
import csv
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

csv_path = sys.argv[1] if len(sys.argv) > 1 else "logs/cwnd_log.csv"
out_path = sys.argv[2] if len(sys.argv) > 2 else "cwnd_graph.png"

times, cwnds, ssthreshs, events = [], [], [], []
with open(csv_path) as f:
    reader = csv.DictReader(f)
    for row in reader:
        times.append(float(row['time_ms']))
        cwnds.append(float(row['cwnd']))
        ssthreshs.append(float(row['ssthresh']))
        events.append(row['event'])

fig, ax = plt.subplots(figsize=(11, 5.5))
ax.plot(times, cwnds, linewidth=1.3, color='#1f77b4', label='cwnd')
ax.plot(times, ssthreshs, linewidth=1, color='#ff7f0e', linestyle='--', label='ssthresh')

# Mark loss events distinctly
for t, c, e in zip(times, cwnds, events):
    if e == 'TIMEOUT':
        ax.plot(t, c, 'rx', markersize=7, markeredgewidth=1.5)
    elif e == 'FAST_RETRANSMIT':
        ax.plot(t, c, 'o', color='purple', markersize=5)

ax.set_xlabel('Time (ms)')
ax.set_ylabel('Window size (packets)')
ax.set_title('Poise AIMD Congestion Window Over Time')
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

# Add a small legend note for the event markers
ax.plot([], [], 'rx', label='Timeout (loss)')
ax.plot([], [], 'o', color='purple', label='Fast retransmit')
ax.legend(loc='upper right', fontsize=9)

plt.tight_layout()
plt.savefig(out_path, dpi=120)
print(f"Saved graph to {out_path}")
print(f"Total events: {len(times)}, Timeouts: {events.count('TIMEOUT')}, "
      f"Fast retransmits: {events.count('FAST_RETRANSMIT')}")
