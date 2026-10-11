/*
 * smart_client: command-line front end of librudp (src/rudp_sender.c, API in src/rudp.h).
 *   --mode aimd|cubic|rl|rl2|deep   congestion-control policy (deep = neural agent, needs --dfile)
 *   --file PATH                     send a real file instead of synthetic test packets
 *   --emu 0                         use the real network (default: emulate the path in this process)
 *   --vt 1                          virtual time: simulated clock and receiver, deterministic and fast
 * Run with any option to see the full list in the source; the evaluation scripts (eval_*.sh, web/vt*.py) drive this program.
 * Output: one RESULT line (scenario,mode,ms,packets,sent,retx,timeouts,fast,avg_rtt,min_rtt,pps,cwnd,reward).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rudp.h"

int main(int argc, char **argv) {
    rudp_options_t o; rudp_options_default(&o);
    o.emulate = 1;                      /* the CLI emulates the path by default (the library default is the real network) */
    const char *fpath = NULL; int use_sack = 1;
    for (int i = 1; i < argc; i++) {
#define ARG(n) (!strcmp(argv[i], n) && i + 1 < argc)
        if (ARG("--mode")) o.mode = argv[++i];
        else if (ARG("--port")) o.port = atoi(argv[++i]);
        else if (ARG("--delay")) o.delay_ms = atof(argv[++i]);
        else if (ARG("--jitter")) o.jitter_ms = atof(argv[++i]);
        else if (ARG("--loss")) o.loss_pct = atof(argv[++i]);
        else if (ARG("--rate")) o.rate_pps = atof(argv[++i]);
        else if (ARG("--queue")) o.queue_packets = atoi(argv[++i]);
        else if (ARG("--packets")) o.packets = atoi(argv[++i]);
        else if (ARG("--file")) fpath = argv[++i];
        else if (ARG("--vt")) o.virtual_time = atoi(argv[++i]);
        else if (ARG("--vstep")) o.vstep_ms = atof(argv[++i]);
        else if (ARG("--pace")) o.pace = atof(argv[++i]);
        else if (ARG("--polite")) o.polite = atof(argv[++i]);   /* neural agent: when the RTT ratio is >= X (queue building) never grow, back off x0.85 (Vegas-like courtesy) */
        else if (ARG("--ssdeep")) o.slow_start_to = atof(argv[++i]);
        else if (ARG("--ssratio")) o.slow_start_ratio = atof(argv[++i]);
        else if (ARG("--seed")) o.seed = (unsigned)atoi(argv[++i]);
        else if (ARG("--qfile")) o.qfile = argv[++i];
        else if (ARG("--train")) o.train = atoi(argv[++i]);
        else if (ARG("--eps")) o.eps = atof(argv[++i]);
        else if (ARG("--trace")) o.trace_file = argv[++i];
        else if (ARG("--emu")) o.emulate = atoi(argv[++i]);
        else if (ARG("--vcomp")) o.vcomp = atoi(argv[++i]);          /* virtual time: add a backlogged AIMD competitor on the same bottleneck */
        else if (ARG("--vcomp-to")) o.vcomp_timeout = atof(argv[++i]);
        else if (ARG("--vcross")) sscanf(argv[++i], "%lf:%lf:%lf", &o.vcross_on, &o.vcross_off, &o.vcross_pps);   /* virtual time: bursty cross traffic ON_MS:OFF_MS:PPS */
        else if (ARG("--scenario")) o.scenario = argv[++i];
        else if (ARG("--maxcwnd")) o.max_cwnd = atof(argv[++i]);
        else if (ARG("--pcap")) o.pcap_file = argv[++i];
        else if (ARG("--ref")) o.ref_rate = atof(argv[++i]);
        else if (ARG("--dyn")) o.dyn = argv[++i];
        else if (ARG("--reorder")) o.reorder = atoi(argv[++i]);
        else if (ARG("--sack")) use_sack = atoi(argv[++i]);
        else if (ARG("--gbn")) use_sack = 0;   /* old Go-Back-N behaviour (use with a server built with -DRUDP_GBN) */
        else if (ARG("--init")) o.init_cwnd = atof(argv[++i]);
        else if (ARG("--tcut")) o.tcut = atof(argv[++i]);
        else if (ARG("--ssthresh")) o.ssthresh = atof(argv[++i]);   /* initial slow-start threshold (default 16, the original value) */
        else if (ARG("--hybrid")) o.hybrid = atof(argv[++i]);   /* agent hands control to AIMD while the per-interval loss fraction is >= X (e.g. 0.05) */
        else if (ARG("--dfile")) o.weights_file = argv[++i];
        else { fprintf(stderr, "bad arg %s\n", argv[i]); return 2; }
    }
    o.go_back_n = !use_sack;
    rudp_stats_t st;
    int rc = fpath ? rudp_send_file(&o, fpath, &st) : rudp_send_test_pattern(&o, &st);
    if (rc != RUDP_OK) return -rc;      /* exit codes 1 (I/O), 2 (arguments), 3 (stuck), as before the library split */
    if (fpath) printf("FILE bytes=%ld fnv1a=%016llx packets=%d\n", st.bytes, (unsigned long long)st.fnv1a, st.packets);
    if (st.link_share >= 0) printf("VTSHARE,%ld,%ld,%.4f\n", st.share_ours, st.share_comp, st.link_share);
    printf("RESULT,%s,%s,%.1f,%d,%ld,%ld,%ld,%ld,%.2f,%.2f,%.1f,%.2f,%.4f\n",
           o.scenario, o.mode, st.elapsed_ms, st.packets, st.transmissions, st.retransmissions, st.timeouts, st.fast_retransmits,
           st.avg_rtt_ms, st.min_rtt_ms, st.goodput_pps, st.final_cwnd, st.avg_reward);
    return 0;
}
