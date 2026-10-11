/*
 * rudp.h - public API of the RUDP sender library (librudp).
 *
 * RUDP is a reliable, congestion-controlled transport over UDP: handshake, Selective Repeat with a 256-packet window, CRC-16,
 * adaptive retransmission timer, and a choice of congestion-control policies (AIMD, CUBIC, a small neural network).
 * The receiving side is the server in src/server.c (bin/server), which writes the delivered bytes to a file (RUDP_OUT) or prints them.
 *
 *   rudp_options_t o; rudp_options_default(&o);
 *   o.port = 8888; o.mode = "deep"; o.weights_file = "results/dqn_chat.txt";
 *   rudp_stats_t st;
 *   int rc = rudp_send(&o, buf, len, &st);          // blocks until every byte is acknowledged
 *   if (rc != RUDP_OK) fprintf(stderr, "%s\n", rudp_strerror(rc));
 *
 * Limits and caveats: one transfer per call (connect, send, close); up to 32 MB per call; payloads are cut into 512-byte packets;
 * not thread-safe (state is process-global, call from one thread at a time); calls may be made one after another in one process.
 */
#ifndef RUDP_H
#define RUDP_H
#include <stddef.h>
#include <stdint.h>

#define RUDP_VERSION "1.0.0"
#define RUDP_MAX_BYTES (32u * 1024u * 1024u)

enum { RUDP_OK = 0, RUDP_ERR_IO = -1, RUDP_ERR_ARGS = -2, RUDP_ERR_STUCK = -3 };

typedef struct {
    /* where to send */
    const char *host;            /* IPv4 address of the server (default "127.0.0.1") */
    int port;                    /* server UDP port (default 8888) */
    /* congestion control */
    const char *mode;            /* "aimd" (default), "cubic", "deep" (neural agent; needs weights_file), "rl2" (table agent; needs qfile) */
    const char *weights_file;    /* exported network for mode "deep" (results/dqn_chat.txt) */
    const char *qfile;           /* Q-table for mode "rl2" */
    double max_cwnd;             /* window cap in packets; 0 = mode default (128; at most 250 with Selective Repeat) */
    double init_cwnd;            /* initial window; < 0 = mode default (10 for the learned modes, 1 otherwise) */
    double ssthresh;             /* initial slow-start threshold (default 16) */
    double polite;               /* neural agent: back off when the RTT ratio is at least this (0 = off, 1.3 = recommended) */
    int go_back_n;               /* 1 = old Go-Back-N behaviour (pair with a server built with -DRUDP_GBN); default 0 = Selective Repeat */
    /* advanced neural-agent settings (negative = default) */
    double pace;                 /* pacing factor, 0 = mode default, -1 = off */
    double slow_start_to;        /* slow-start phase up to this window (-1 = default 64, 0 = off) */
    double slow_start_ratio;     /* leave slow start when the RTT ratio reaches this (default 1.3) */
    double hybrid;               /* hand over to AIMD at this interval loss fraction (-1 = default 0.10, 0 = off) */
    /* path emulation inside the process, for experiments (default off: use the real network) */
    int emulate; double delay_ms, jitter_ms, loss_pct, rate_pps; int queue_packets; int reorder;
    /* experiment and debugging aids */
    unsigned seed;               /* random seed (default 1) */
    int packets;                 /* synthetic mode only: number of 40-byte packets when no data is given (default 1500) */
    const char *scenario;        /* label printed in the RESULT line */
    const char *trace_file;      /* write a CSV trace of window and RTT */
    const char *pcap_file;       /* write a receiver-side pcap */
    int virtual_time;            /* 1 = simulated clock and in-process receiver (no network); used by the evaluation scripts */
    double vstep_ms;             /* virtual-time step (default 0.35) */
    const char *dyn;             /* virtual time: time-varying path "T_MS:RATE_PPS:DELAY_MS;..." */
    int vcomp; double vcomp_timeout; double vcross_on, vcross_off, vcross_pps;   /* virtual-time competitors */
    int train; double eps; double tcut; double ref_rate;                    /* Q-learning training and legacy options */
} rudp_options_t;

typedef struct {
    double elapsed_ms;           /* time from the first send to the last acknowledgment */
    int packets;                 /* packets in the transfer */
    long bytes;                  /* payload bytes */
    uint64_t fnv1a;              /* FNV-1a hash of the payload, to compare with the server's TRANSFER line */
    long transmissions;          /* packets put on the wire, including retransmissions */
    long retransmissions, timeouts, fast_retransmits;
    double avg_rtt_ms, min_rtt_ms, goodput_pps, final_cwnd, avg_reward;
    double link_share;           /* virtual time with vcomp: this flow's share of the bottleneck (else -1) */
    long share_ours, share_comp;
} rudp_stats_t;

void rudp_options_default(rudp_options_t *o);
/* Send len bytes (1 .. RUDP_MAX_BYTES) to the server. Returns RUDP_OK or a negative RUDP_ERR_* code. stats may be NULL. */
int rudp_send(const rudp_options_t *o, const void *data, size_t len, rudp_stats_t *stats);
int rudp_send_file(const rudp_options_t *o, const char *path, rudp_stats_t *stats);
/* Send o->packets synthetic 40-byte test packets (used by the evaluation scripts). */
int rudp_send_test_pattern(const rudp_options_t *o, rudp_stats_t *stats);
const char *rudp_strerror(int code);

#endif
