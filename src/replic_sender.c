/*
 * libreplic: the REPLIC sender (reliability machinery plus a pluggable congestion controller). The public API is in replic.h; the command-line
 * front end is smart_client.c. Original notes follow.
 *
 * REPLIC client: REPLIC sender with a pluggable congestion controller.
 *   --mode aimd : rule-based slow start + AIMD (the original REPLIC policy)
 *   --mode rl   : Q-learning cwnd controller (rl_cc.h)
 *   --mode rl2  : the chat-style table agent (no-shrink rule, window cap 32, decisions every 100-500 ms)
 *   --mode deep : the neural agent (deep_cc.h), same decision loop as rl2
 *   --reorder 1 : let jitter reorder packets on the emulated forward path
 *   --sack 1    : Selective Repeat (default): resend only holes, using the bitmap in the ACKs; --gbn restores Go-Back-N (server built with -DREPLIC_GBN)
 *   --init N    : initial window (default 1; the chat-style modes default to 10)
 * Both modes share the same reliability machinery (Go-Back-N, adaptive RTO,
 * dup-ACK fast retransmit that resends the window) so only the cwnd policy differs.
 * The network path (delay, jitter, random loss, bottleneck rate, tail-drop queue) is
 * emulated inside this process, so results do not need tc/netem or root.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <unistd.h>
#include <time.h>
#include <poll.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include "common.h"
#include "rl_cc.h"
#include "deep_cc.h"
#if defined(__has_include)
#if __has_include("neural_weights.h")
#define SM_HAVE_BUILTIN        /* weights compiled into the binary (src/neural_weights.h, written by scripts/train_smart_replic.py --export) */
#endif
#endif
#include "smart_cc.h"
#include "replic.h"

#define PKT_BYTES 40
#define MAXN 65536   /* chunks in file mode */
#define QCAP 8192

typedef struct { double t; int len; uint8_t buf[REPLIC_HEADER_SIZE + MAX_PAYLOAD]; } pend_t;
typedef struct { pend_t *a; int head, tail; } ring_t;

/* --vt 1: virtual time. The server is simulated in-process and the clock advances by a fixed step per loop
   iteration (the real loop sleeps ~0.3 ms), so a 20 s transfer takes milliseconds and results are reproducible. */
static int vt = 0; static double vclock = 0;
static double now_ms(void) {
    if (vt) return vclock;
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}
static double urand(void) { return rand() / (RAND_MAX + 1.0); }
static void rpush(ring_t *r, double t, const uint8_t *b, int len) {
    pend_t *p = &r->a[r->tail % QCAP]; p->t = t; p->len = len; memcpy(p->buf, b, (size_t)len); r->tail++;
}
/* Earliest-arriving packet first. With an in-order path (the default) this is the head of the queue; with --reorder 1 a packet
   delayed by jitter is overtaken by later ones, as on a real netem path. */
static pend_t *rfront(ring_t *r) {
    if (r->head >= r->tail) return NULL;
    int bi = r->head;
    for (int i = r->head + 1; i < r->tail; i++) if (r->a[i % QCAP].t < r->a[bi % QCAP].t) bi = i;
    if (bi != r->head) { pend_t tmp = r->a[bi % QCAP]; r->a[bi % QCAP] = r->a[r->head % QCAP]; r->a[r->head % QCAP] = tmp; }
    return &r->a[r->head % QCAP];
}


/* ---- optional pcap export (raw-IP linktype 101): what a capture at the receiver would see ---- */
static FILE *pcapf = NULL; static double pcap_epoch = 0;
static uint16_t ipsum(const uint8_t *h, int n) { uint32_t a = 0; for (int i = 0; i < n; i += 2) a += (h[i] << 8) | h[i+1]; while (a >> 16) a = (a & 0xffff) + (a >> 16); return (uint16_t)~a; }
static void pcap_open(const char *path) {
    pcapf = fopen(path, "wb"); if (!pcapf) return;
    uint32_t m = 0xa1b2c3d4; uint16_t vM = 2, vm = 4; int32_t z = 0; uint32_t sl = 65535, lt = 101;
    fwrite(&m,4,1,pcapf); fwrite(&vM,2,1,pcapf); fwrite(&vm,2,1,pcapf); fwrite(&z,4,1,pcapf); fwrite(&z,4,1,pcapf); fwrite(&sl,4,1,pcapf); fwrite(&lt,4,1,pcapf);
    pcap_epoch = 1760000000.0;  /* arbitrary fixed epoch; only relative times matter */
}
/* to_server=1: client 192.168.10.10:40000 -> server 192.168.30.10:port ; else the reverse */
static void pcap_write(double t_ms, int to_server, int port, const uint8_t *pl, int n) {
    if (!pcapf) return;
    uint8_t pk[2048]; int tot = 20 + 8 + n; memset(pk, 0, 28);
    pk[0] = 0x45; pk[2] = tot >> 8; pk[3] = tot & 255; pk[8] = 62; pk[9] = 17;
    const uint8_t c[4] = {192,168,10,10}, sv[4] = {192,168,30,10};
    memcpy(pk + 12, to_server ? c : sv, 4); memcpy(pk + 16, to_server ? sv : c, 4);
    uint16_t cs = ipsum(pk, 20); pk[10] = cs >> 8; pk[11] = cs & 255;
    uint16_t sp = to_server ? 40000 : (uint16_t)port, dp = to_server ? (uint16_t)port : 40000, ul = (uint16_t)(8 + n);
    pk[20] = sp >> 8; pk[21] = sp & 255; pk[22] = dp >> 8; pk[23] = dp & 255; pk[24] = ul >> 8; pk[25] = ul & 255;
    memcpy(pk + 28, pl, (size_t)n);
    double ts = pcap_epoch + t_ms / 1000.0; uint32_t sec = (uint32_t)ts, us = (uint32_t)((ts - sec) * 1e6), l = (uint32_t)tot;
    fwrite(&sec,4,1,pcapf); fwrite(&us,4,1,pcapf); fwrite(&l,4,1,pcapf); fwrite(&l,4,1,pcapf); fwrite(pk, 1, (size_t)tot, pcapf);
    if (getenv("REPLIC_LIVE")) fflush(pcapf);
}

/* ---- interpretability hooks (environment variables, off by default) ----
   REPLIC_STATELOG=file : append every neural decision "x0..x4,lo,act" for the distillation study
   REPLIC_NOISE=sigma   : add Gaussian noise N(0,sigma) to the four measured inputs (robustness test)
   REPLIC_TREE=file     : replace the network by a distilled decision tree ("S feat thr left right" / "L action" lines) */
typedef struct { int feat, left, right, act; double thr; } tnode_t;
static tnode_t tree[512]; static int tree_n = 0;
static int tree_load(const char *path) {
    FILE *f = fopen(path, "r"); if (!f) return -1; char t; tree_n = 0;
    while (tree_n < 512 && fscanf(f, " %c", &t) == 1) {
        tnode_t *n = &tree[tree_n++]; memset(n, 0, sizeof *n);
        if (t == 'S') { if (fscanf(f, "%d %lf %d %d", &n->feat, &n->thr, &n->left, &n->right) != 4) { fclose(f); return -1; } n->act = -1; }
        else if (fscanf(f, "%d", &n->act) != 1) { fclose(f); return -1; }
    }
    fclose(f); return tree_n > 0 ? 0 : -1;
}
static int tree_predict(const double *x) { int i = 0; while (tree[i].act < 0) i = x[tree[i].feat] <= tree[i].thr ? tree[i].left : tree[i].right; return tree[i].act; }
static double gauss(void) { double u = urand(), v = urand(); if (u < 1e-12) u = 1e-12; return sqrt(-2 * log(u)) * cos(6.283185307179586 * v); }

/* ---- emulated path parameters ---- */
static double delay_ms = 10, jitter_ms = 0, loss_pct = 0, rate_pps = 0;
static int queue_cap = 50;
static int emu = 1, reorder = 0;
static double last_depart = 0, last_arrive_f = 0, last_arrive_r = 0;
static double depart_q[QCAP]; static int dq_h = 0, dq_t = 0;
static long drops_random = 0, drops_queue = 0, comp_ok = 0, ours_ok = 0;

/* --dyn "T:RATE:DELAY;T:RATE:DELAY;..." : time-varying path. From T ms (transfer clock) on, the bottleneck rate (pkt/s) and one-way delay (ms)
   take the given values; RATE or DELAY of 0 or less keeps the current one. Used to test bandwidth drops, RTT jumps and satellite hand-overs. */
#define DYN_MAX 128
static struct { double t, rate, delay; } dyn[DYN_MAX]; static int dyn_n = 0, dyn_i = 0;
static void dyn_parse(const char *s) {
    dyn_n = dyn_i = 0;
    while (s && *s && dyn_n < DYN_MAX) {
        double t, r, d; int used = 0;
        if (sscanf(s, "%lf:%lf:%lf%n", &t, &r, &d, &used) < 3) break;
        dyn[dyn_n].t = t; dyn[dyn_n].rate = r; dyn[dyn_n].delay = d; dyn_n++;
        s += used; if (*s == ';') s++;
    }
}
static void dyn_apply(double now) {
    while (dyn_i < dyn_n && dyn[dyn_i].t <= now) {
        if (dyn[dyn_i].rate > 0) rate_pps = dyn[dyn_i].rate;
        if (dyn[dyn_i].delay > 0) delay_ms = dyn[dyn_i].delay;
        dyn_i++;
    }
}
static double jit(void) { return jitter_ms > 0 ? (urand() * 2 - 1) * jitter_ms : 0; }

/* Returns arrival time at the server, or -1 if the path drops the packet. */
static double fwd_path(double now) {
    if (!emu) return now;
    if (dyn_n) dyn_apply(now);
    if (loss_pct > 0 && urand() * 100.0 < loss_pct) { drops_random++; return -1; }
    double depart = now;
    if (rate_pps > 0) {
        while (dq_h < dq_t && depart_q[dq_h % QCAP] <= now) dq_h++;
        if (dq_t - dq_h >= queue_cap) { drops_queue++; return -1; }
        double svc = 1000.0 / rate_pps;
        depart = (last_depart > now ? last_depart : now) + svc;
        last_depart = depart; depart_q[dq_t++ % QCAP] = depart;
    }
    ours_ok++;
    double arr = depart + delay_ms + jit();
    if (!reorder && arr < last_arrive_f) arr = last_arrive_f;      /* keep path in-order unless --reorder 1 */
    last_arrive_f = arr; return arr;
}
static double rev_path(double now) {
    if (dyn_n) dyn_apply(now);
    if (!emu) return now;
    double arr = now + delay_ms + jit();
    if (!reorder && arr < last_arrive_r) arr = last_arrive_r;
    last_arrive_r = arr; return arr;
}


/* ---- --vcomp 1 (virtual time only): an infinitely backlogged AIMD competitor (cap 128) shares the same bottleneck, so
   fairness can be measured and trained against. Packet-level: its own window, loss detection one round trip after a drop. ---- */
static double comp_to = 0.0;   /* fraction of loss events the competitor treats as a timeout (window to 1), calibrated so AIMD against it splits the link about evenly */
static int comp_on = 0; static double comp_cwnd = 10, comp_ssth = 128, comp_cut_until = 0; 
typedef struct { double t; int lost; } cinfl_t; static cinfl_t comp_q[1024]; static int comp_h = 0, comp_t = 0;
static void comp_step(double now) {
    while (comp_h < comp_t && comp_q[comp_h % 1024].t <= now) {
        cinfl_t e = comp_q[comp_h++ % 1024];
        if (e.lost) { if (now >= comp_cut_until) { comp_ssth = comp_cwnd / 2 < 2 ? 2 : comp_cwnd / 2; comp_cwnd = urand() < comp_to ? 1.0 : comp_ssth; comp_cut_until = now + 2 * delay_ms + (last_depart > now ? last_depart - now : 0); } }
        else comp_cwnd += comp_cwnd < comp_ssth ? 1.0 : 1.0 / comp_cwnd;
        if (comp_cwnd > 128) comp_cwnd = 128;
    }
    while (comp_t - comp_h < (int)comp_cwnd && comp_t - comp_h < 1000) {
        int lost = 0; double depart = now;
        if (loss_pct > 0 && urand() * 100.0 < loss_pct) lost = 1;
        else if (rate_pps > 0) {
            while (dq_h < dq_t && depart_q[dq_h % QCAP] <= now) dq_h++;
            if (dq_t - dq_h >= queue_cap) lost = 1;
            else { depart = (last_depart > now ? last_depart : now) + 1000.0 / rate_pps; last_depart = depart; depart_q[dq_t++ % QCAP] = depart; }
        }
        double t = lost ? now + 2 * delay_ms + (last_depart > now ? last_depart - now : 0) : depart + 2 * delay_ms;
        if (!lost) comp_ok++;
        comp_q[comp_t++ % 1024] = (cinfl_t){ t, lost };
    }
}


/* ---- --vcross ON:OFF:PPS (virtual time only): bursty cross traffic on the bottleneck, same model as bin/impair --cross
   (exponentially distributed on and off periods; during "on" packets arrive at PPS and occupy the shared queue) ---- */
static double xv_on = 0, xv_off = 0, xv_pps = 0, xv_end = -1, xv_next = 0; static int xv_is_on = 0;
static double xv_exp(double mean) { return -mean * log(1 - urand()); }
static void cross_step(double now) {
    if (xv_pps <= 0 || rate_pps <= 0) return;
    if (xv_end < 0) xv_end = now + xv_exp(xv_off);
    while (now >= xv_end) { xv_is_on = !xv_is_on; xv_end += xv_exp(xv_is_on ? xv_on : xv_off); xv_next = now; }
    if (!xv_is_on) return;
    while (xv_next <= now) {
        while (dq_h < dq_t && depart_q[dq_h % QCAP] <= now) dq_h++;
        if (dq_t - dq_h < queue_cap) { double dep = (last_depart > now ? last_depart : now) + 1000.0 / rate_pps; last_depart = dep; depart_q[dq_t++ % QCAP] = dep; }
        xv_next += 1000.0 / xv_pps;
    }
}

/* ---- in-process receiver for --vt 1: same behaviour as server.c (256-packet Selective Repeat window + 32-byte bitmap,
   or Go-Back-N when use_sack is off) ---- */
static uint32_t v_exp = 0; static uint8_t v_have[256]; static int v_sr = 1;
static void vsrv_rx(ring_t *rev, double now, const uint8_t *b, int n) {
    replic_packet_t p; if (replic_unpack(b, (size_t)n, &p) != 0 || !(p.flags & FLAG_DATA)) return;
    if (p.seq_num == v_exp) { v_exp++; if (v_sr) while (v_have[v_exp % 256]) { v_have[v_exp % 256] = 0; v_exp++; } }
    else if (v_sr && p.seq_num > v_exp && p.seq_num - v_exp < 256) v_have[p.seq_num % 256] = 1;
    replic_packet_t a; memset(&a, 0, sizeof a); a.ack_num = v_exp; a.flags = FLAG_ACK;
    if (v_sr) { a.payload_len = 32; for (int j = 1; j < 256; j++) if (v_have[(v_exp + (uint32_t)j) % 256]) a.payload[j / 8] |= (uint8_t)(1u << (j % 8)); }
    uint8_t ob[BUFFER_SIZE]; int l = replic_pack(&a, ob, sizeof ob);
    double arr = rev_path(now); rpush(rev, arr, ob, l);
}


static int run_transfer(const replic_options_t *o, const char *fpath, const void *data, size_t datalen, replic_stats_t *res) {
    const char *mode = o->mode ? o->mode : "aimd", *scenario = o->scenario ? o->scenario : "custom", *qfile = o->qfile, *trace = o->trace_file, *pcapp = o->pcap_file, *dfile = o->weights_file;
    int port = o->port > 0 ? o->port : SERVER_PORT, npk = o->packets > 0 ? o->packets : 1500, train = o->train;
    unsigned seed = o->seed ? o->seed : 1;
    (void)scenario;
    double polite = o->polite, pace = o->pace, pace_next = 0, vstep = o->vstep_ms > 0 ? o->vstep_ms : 0.35, ssdeep = o->slow_start_to, ss_ratio = o->slow_start_ratio > 0 ? o->slow_start_ratio : 1.3;
    long fbytes = 0; uint64_t ffnv = 1469598103934665603ULL;
    int use_sack = !o->go_back_n; double hyb = o->hybrid, ss0 = o->ssthresh > 0 ? o->ssthresh : 16.0; int guard = 0, cap_set = o->max_cwnd > 0;
    double tcut = o->tcut, eps = o->eps, maxcwnd = cap_set ? o->max_cwnd : 128, ref = o->ref_rate, init_cwnd = o->init_cwnd;
    /* process-global path-emulation and virtual-time state, reset for every transfer */
    delay_ms = o->delay_ms; jitter_ms = o->jitter_ms; loss_pct = o->loss_pct; rate_pps = o->rate_pps; queue_cap = o->queue_packets > 0 ? o->queue_packets : 50;
    emu = o->emulate; reorder = o->reorder; vt = o->virtual_time; vclock = 0;
    last_depart = last_arrive_f = last_arrive_r = 0; dq_h = dq_t = 0; drops_random = drops_queue = comp_ok = ours_ok = 0;
    comp_on = o->vcomp; comp_to = o->vcomp_timeout; comp_cwnd = 10; comp_ssth = 128; comp_cut_until = 0; comp_h = comp_t = 0;
    xv_on = o->vcross_on; xv_off = o->vcross_off; xv_pps = o->vcross_pps; xv_end = -1; xv_next = 0; xv_is_on = 0;
    dyn_parse(o->dyn);
    v_exp = 0; memset(v_have, 0, sizeof v_have); pcapf = NULL;
    int use_cubic = !strcmp(mode, "cubic");   /* CUBIC window growth (Ha, Rhee, Xu 2008), loss response x0.7 */
    int use_rl = !strcmp(mode, "rl");
    int use_bbr = !strcmp(mode, "bbr");   /* simplified BBRv1 (Cardwell et al. 2016): model-based, ignores loss; see results/bbr_*.txt */
    int use_deep = !strcmp(mode, "deep"), use_rl2 = !strcmp(mode, "rl2"), use_smart = !strcmp(mode, "smart"), chat = use_deep || use_rl2 || use_smart;
    if (use_rl2 && !cap_set && maxcwnd > 32) maxcwnd = 32;   /* the table agent expects a cap of 32 */
    int classic = getenv("REPLIC_CLASSIC") != NULL;               /* REPLIC_CLASSIC=1: the original agent settings (cap 32, no hand-over) used by the older evaluation scripts */
    if ((use_deep || use_smart) && pace == 0 && !classic) pace = 2.0;   /* neural agent paces new packets at 2 x cwnd/srtt: 3% faster, 23% fewer retransmissions (vt sweep) */
    if (use_smart && ssdeep < 0) ssdeep = 64;
    if (use_deep && ssdeep < 0) ssdeep = classic ? 0 : 64;   /* slow start up to 64 packets before the policy takes over (see web/vt_random.py sweep) */
    if (use_smart && !cap_set) maxcwnd = 128;
    if (use_deep && !cap_set) maxcwnd = classic ? 32 : 128;                /* performance default for the neural agent (it was trained with a cap of 32 but generalises; see experiments/archive/eval_perf2.sh) */
    if (use_smart) hyb = 0;                      /* REPLIC: no external AIMD hand-over, no polite guard; the policy alone decides */
    if (use_deep && hyb < 0) hyb = classic ? 0.0 : 0.10;                     /* performance default: hand over to AIMD under heavy loss; --hybrid 0 turns it off */
    if (use_sack && maxcwnd > 250) maxcwnd = 250;   /* the receiver buffers a 256-packet window */
    if (init_cwnd < 0) init_cwnd = chat ? 10.0 : 1.0;
    sm_net_t smnet; sm_hist_t smh; sm_iv_t smiv; sm_hist_init(&smh); memset(&smiv, 0, sizeof smiv);
    if (use_smart) {
        if (dfile) { int lr = sm_load(&smnet, dfile); if (lr) { fprintf(stderr, "cannot load smart weights %s (%d)\n", dfile, lr); return REPLIC_ERR_ARGS; } }
#ifdef SM_HAVE_BUILTIN
        else sm_builtin(&smnet);
#else
        else { fprintf(stderr, "--mode smart needs --dfile smart_*.txt (no compiled-in weights)\n"); return REPLIC_ERR_ARGS; }
#endif
    }
    deep_net_t dnet; if (use_deep && (!dfile || deep_load(&dnet, dfile) != 0)) { fprintf(stderr, "need --dfile with the exported network\n"); return REPLIC_ERR_ARGS; }
    if (npk > MAXN) npk = MAXN;
    if (queue_cap > QCAP / 2) queue_cap = QCAP / 2;
    if (ref <= 0) ref = rate_pps > 0 ? rate_pps : 1000;
    srand(seed); v_sr = use_sack;
    if (pcapp) pcap_open(pcapp);

    rl_agent_t ag; int have_q = 0;
    rl_init(&ag, 0.15, 0.9, train ? eps : 0.0);
    if ((use_rl || use_rl2) && qfile && rl_load(&ag, qfile) == 0) have_q = 1;
    (void)have_q;
    ag.eps = train ? eps : 0.0;

    int sockfd = vt ? -1 : socket(AF_INET, SOCK_DGRAM, 0);
    if (!vt && sockfd < 0) { perror("socket"); return REPLIC_ERR_IO; }
    struct sockaddr_in sa; memset(&sa, 0, sizeof sa);
    sa.sin_family = AF_INET; sa.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, o->host ? o->host : SERVER_IP, &sa.sin_addr) != 1) { fprintf(stderr, "bad host address\n"); return REPLIC_ERR_ARGS; }
    struct timeval tv = {1, 0}; if (!vt) setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

    /* ---- handshake (direct, not emulated) ---- */
    uint32_t cseq = (uint32_t)rand(), sseq = 0; uint8_t buf[BUFFER_SIZE]; int ok = 0;
    if (vt) { cseq = 1000; ok = 1; }
    for (int at = 0; at < 8 && !ok; at++) {
        replic_packet_t syn; memset(&syn, 0, sizeof syn); syn.seq_num = cseq; syn.flags = FLAG_SYN;
        int l = replic_pack(&syn, buf, sizeof buf);
        sendto(sockfd, buf, (size_t)l, 0, (struct sockaddr *)&sa, sizeof sa);
        ssize_t n = recvfrom(sockfd, buf, sizeof buf, 0, NULL, NULL);
        replic_packet_t rp;
        if (n > 0 && replic_unpack(buf, (size_t)n, &rp) == 0 && (rp.flags & FLAG_SYN) &&
            (rp.flags & FLAG_ACK) && rp.ack_num == cseq + 1) {
            sseq = rp.seq_num;
            replic_packet_t ack; memset(&ack, 0, sizeof ack);
            ack.seq_num = cseq + 1; ack.ack_num = sseq + 1; ack.flags = FLAG_ACK;
            l = replic_pack(&ack, buf, sizeof buf);
            sendto(sockfd, buf, (size_t)l, 0, (struct sockaddr *)&sa, sizeof sa);
            ok = 1;
        }
    }
    if (!ok) { fprintf(stderr, "handshake failed (port %d)\n", port); return REPLIC_ERR_IO; }

    /* ---- packets ---- */
    static replic_packet_t pk[MAXN];
    static double sent_at[MAXN]; static uint8_t retx_flag[MAXN], sacked[MAXN];
    memset(sent_at, 0, sizeof sent_at); memset(retx_flag, 0, sizeof retx_flag); memset(sacked, 0, sizeof sacked);
    uint32_t base_seq = cseq + 1; v_exp = base_seq;
    FILE *ff = NULL;
    if (fpath) {                                   /* file mode: send a real file in 512-byte chunks */
        ff = fopen(fpath, "rb"); if (!ff) { perror(fpath); return REPLIC_ERR_IO; }
        fseek(ff, 0, SEEK_END); long sz = ftell(ff); fseek(ff, 0, SEEK_SET);
        if (sz <= 0 || sz > (long)MAXN * MAX_PAYLOAD) { fprintf(stderr, "file must be 1 byte to %d MB\n", MAXN * MAX_PAYLOAD >> 20); fclose(ff); return REPLIC_ERR_ARGS; }
        npk = (int)((sz + MAX_PAYLOAD - 1) / MAX_PAYLOAD);
    } else if (data) {                             /* buffer mode (library): the bytes are cut into 512-byte chunks */
        if (datalen == 0 || datalen > REPLIC_MAX_BYTES) { fprintf(stderr, "data must be 1 byte to %u MB\n", REPLIC_MAX_BYTES >> 20); return REPLIC_ERR_ARGS; }
        npk = (int)((datalen + MAX_PAYLOAD - 1) / MAX_PAYLOAD);
    }
    for (int i = 0; i < npk; i++) {
        memset(&pk[i], 0, sizeof pk[i]);
        pk[i].seq_num = base_seq + (uint32_t)i; pk[i].flags = FLAG_DATA;
        if (ff) {
            size_t got = fread(pk[i].payload, 1, MAX_PAYLOAD, ff); pk[i].payload_len = (uint16_t)got; fbytes += (long)got;
            for (size_t j = 0; j < got; j++) { ffnv ^= pk[i].payload[j]; ffnv *= 1099511628211ULL; }
        } else if (data) {
            size_t off = (size_t)i * MAX_PAYLOAD, n = datalen - off < MAX_PAYLOAD ? datalen - off : MAX_PAYLOAD;
            memcpy(pk[i].payload, (const uint8_t *)data + off, n); pk[i].payload_len = (uint16_t)n; fbytes += (long)n;
            for (size_t j = 0; j < n; j++) { ffnv ^= pk[i].payload[j]; ffnv *= 1099511628211ULL; }
        } else {
            pk[i].payload_len = PKT_BYTES;
            for (int j = 0; j < PKT_BYTES; j++) pk[i].payload[j] = (uint8_t)('A' + (i + j) % 26);
        }
    }
    if (ff) fclose(ff);
    ring_t fwd = { malloc(sizeof(pend_t) * QCAP), 0, 0 }, rev = { malloc(sizeof(pend_t) * QCAP), 0, 0 };

    FILE *tr = trace ? fopen(trace, "w") : NULL;
    if (tr && getenv("REPLIC_LIVE")) setvbuf(tr, NULL, _IOLBF, 0);   /* Studio: flush every trace line so a browser can follow live */
    if (tr) fprintf(tr, "time_ms,event,cwnd,rtt_ms\n");

    /* ---- sender state ---- */
    int base = 0, next = 0, dup = 0, recover = -1; uint32_t last_ack = 0;
    int ss_phase = 0;   /* deep agent: classic slow start until the first congestion signal, then the policy takes over */
    double rto_mult = getenv("REPLIC_RTOMULT") ? atof(getenv("REPLIC_RTOMULT")) : 1.3; double wmax = 0, kcub = 0, epoch = -1; double cwnd = init_cwnd, ssthresh = ss0, srtt = -1, rttvar = 0, rto = 1000;
    double min_rtt = 1e9, rtt_sum = 0, dev_floor = 1e9; long rtt_n = 0;
    /* BBR state: delivery-rate history, windowed max filter, STARTUP -> DRAIN -> PROBE_BW (ProbeRTT omitted: transfers are short) */
    double bb_t[64] = {0}; long bb_d[64] = {0}; int bb_n = 0; long bb_deliv = 0;
    double bw_t[32] = {0}, bw_v[32] = {0}; int bw_n = 0; double bbr_bw = 0, bbr_gain = 2.885, bbr_full_bw = 0; int bbr_state = 0, bbr_full_cnt = 0, bbr_phase = 0;
    long bbr_round_end = 0; double bbr_phase_start = 0, bbr_rt = 1e9, bbr_rt_t = 0;   /* bbr_rt: min RTT over the last 10 s (BBR's RTprop filter; ProbeRTT is replaced by this expiry) */
    double last_prog = now_ms(); int tlp_sent = 0, rack_on = getenv("REPLIC_NORACK") ? 0 : 1;
    long tx_total = 0, retx = 0, timeouts = 0, fast_retx = 0, fec_tx = 0; int fec_k = getenv("REPLIC_FEC") ? atoi(getenv("REPLIC_FEC")) : 0;
    double timer_start = 0; int timer_on = 0;
    /* RL interval stats */
    double iv_start = 0, iv_rtt_sum = 0; long iv_rtt_n = 0, iv_acked = 0, iv_sent = 0, iv_retx = 0;
    int prev_s = -1, prev_a = -1; double rew_sum = 0; long rew_n = 0;
    long iv_new = 0, iv_lossev = 0; int last_act = 2;

    if ((use_deep || use_smart) && ssdeep > 0) { ss_phase = 1; ssthresh = ssdeep; }
    double t0 = now_ms(); iv_start = t0; timer_start = t0;
    if (tr) fprintf(tr, "0,START,%.2f,0\n", cwnd);

#define TX(i, isretx) do { \
        uint8_t b_[BUFFER_SIZE]; int l_ = replic_pack(&pk[i], b_, sizeof b_); \
        double tn_ = now_ms(); double ar_ = fwd_path(tn_); \
        tx_total++; iv_sent++; if (!(isretx)) iv_new++; if (isretx) { retx++; iv_retx++; retx_flag[i] = 1; } \
        sent_at[i] = tn_; \
        if (ar_ >= 0) rpush(&fwd, ar_, b_, l_); \
        if (!(isretx) && fec_k > 0 && ((i) % fec_k == fec_k - 1 || (i) == npk - 1)) { \
            int gs_ = (i) - (i) % fec_k, n_ = (i) - gs_ + 1; replic_packet_t fp_; memset(&fp_, 0, sizeof fp_); uint32_t lx_ = 0; \
            for (int j_ = gs_; j_ <= (i); j_++) { for (int k_ = 0; k_ < pk[j_].payload_len; k_++) fp_.payload[k_] ^= pk[j_].payload[k_]; lx_ ^= pk[j_].payload_len; } \
            fp_.seq_num = pk[gs_].seq_num; fp_.flags = FLAG_FEC; fp_.payload_len = MAX_PAYLOAD; fp_.ack_num = ((uint32_t)n_ << 16) | lx_; \
            uint8_t fb_[BUFFER_SIZE]; int fl_ = replic_pack(&fp_, fb_, sizeof fb_); double fa_ = fwd_path(tn_); fec_tx++; \
            if (fa_ >= 0 && fl_ > 0) rpush(&fwd, fa_, fb_, fl_); \
        } \
    } while (0)

    while (base < npk) {
        double now = now_ms();
        if (vt && comp_on) comp_step(now);
        if (vt && xv_pps > 0) cross_step(now);
        /* 1. release forward packets whose arrival time has come */
        pend_t *p;
        while ((p = rfront(&fwd)) && p->t <= now) {
            pcap_write(now, 1, port, p->buf, p->len);
            if (vt) vsrv_rx(&rev, now, p->buf, p->len); else sendto(sockfd, p->buf, (size_t)p->len, 0, (struct sockaddr *)&sa, sizeof sa);
            fwd.head++;
        }
        /* 2. drain real socket (server replies) into the delayed return path */
        for (; !vt;) {
            struct pollfd pf = { sockfd, POLLIN, 0 };
            if (poll(&pf, 1, 0) <= 0) break;
            ssize_t n = recvfrom(sockfd, buf, sizeof buf, 0, NULL, NULL);
            if (n <= 0) break;
            rpush(&rev, rev_path(now), buf, (int)n);
        }
        /* 3. process ACKs that have "arrived" */
        while ((p = rfront(&rev)) && p->t <= now) {
            replic_packet_t ack; int okp = replic_unpack(p->buf, (size_t)p->len, &ack) == 0;
            pcap_write(now, 0, port, p->buf, p->len); rev.head++;
            if (!okp || !(ack.flags & FLAG_ACK)) continue;
            uint32_t a = ack.ack_num;
            if (use_sack && ack.payload_len == 32) {      /* 256-packet receive window: bit j = packet a + j is held */
                for (int j = 1; j < 256; j++) if (ack.payload[j / 8] >> (j % 8) & 1) { long ix = (long)(a + (uint32_t)j) - (long)base_seq; if (ix >= 0 && ix < npk) sacked[ix] = 1; }
            }
            if (a > pk[base].seq_num) {
                int nb = base;
                while (nb < npk && pk[nb].seq_num < a) nb++;
                int newly = nb - base;
                if (!retx_flag[nb - 1]) {
                    double rtt = now - sent_at[nb - 1];
                    if (rtt < min_rtt) min_rtt = rtt;
                    if (rtt <= bbr_rt || now - bbr_rt_t > 10000) { bbr_rt = rtt; bbr_rt_t = now; }
                    rtt_sum += rtt; rtt_n++; iv_rtt_sum += rtt; iv_rtt_n++;
                    if (srtt < 0) { srtt = rtt; rttvar = rtt / 2; }
                    else { rttvar = 0.75 * rttvar + 0.25 * fabs(srtt - rtt); srtt = 0.875 * srtt + 0.125 * rtt; }
                    if (rtt_n >= 8 && rttvar < dev_floor) dev_floor = rttvar;
                    rto = srtt + 4 * rttvar; if (rto < srtt * rto_mult) rto = srtt * rto_mult; if (rto < 60) rto = 60; if (rto > 3000) rto = 3000;
                }
                last_prog = now; tlp_sent = 0; iv_acked += newly; if (use_smart) { int bi = (int)((now - iv_start) / SM_BIN_MS); if (bi >= 0 && bi < SM_NBINS) smiv.bins[bi] += newly; } base = nb; dup = 0; last_ack = a;
                if ((!use_rl && !chat && !use_bbr) || guard > 0 || ss_phase) {
                    for (int k = 0; k < newly; k++) {
                        if (cwnd < ssthresh) cwnd += 1.0;
                        else if (use_cubic) {
                            if (epoch < 0) { epoch = now; wmax = cwnd; kcub = 0; }
                            double tt = (now - epoch) / 1000.0, tg = 0.4 * pow(tt - kcub, 3) + wmax;
                            cwnd += tg > cwnd ? (tg - cwnd) / cwnd : 0.01 / cwnd;
                        } else cwnd += 1.0 / cwnd;
                    }
                    if (cwnd > maxcwnd) cwnd = maxcwnd;
                    if (ss_phase && cwnd >= ssdeep) ss_phase = 0;
                    if (ss_phase && rack_on && !getenv("REPLIC_NOHYSTART") && rtt_n >= 8 && srtt > 0 && min_rtt < 1e8 && srtt >= min_rtt * 1.25 + 4 * rttvar) ss_phase = 0;   /* HyStart-style: leave slow start when the queue starts to build, not at the next 100 ms control interval */
                }
                if (use_bbr) {
                    bb_deliv += newly; bb_t[bb_n % 64] = now; bb_d[bb_n % 64] = bb_deliv; bb_n++;
                    double win = bbr_rt < 1e8 ? (bbr_rt < 5 ? 5 : bbr_rt) : 20;
                    for (int q = bb_n - 2; q >= 0 && q >= bb_n - 64; q--) {          /* oldest sample still within one min-RTT window */
                        if (now - bb_t[q % 64] >= win) {
                            if (now - bb_t[q % 64] > 4 * win) break;   /* stale baseline after an ACK gap: rate would be meaninglessly low */
                            double sm = (bb_deliv - bb_d[q % 64]) * 1000.0 / (now - bb_t[q % 64]);
                            bw_t[bw_n % 32] = now; bw_v[bw_n % 32] = sm; bw_n++; break;
                        }
                    }
                    double mx = 0; for (int q = 0; q < 32 && q < bw_n; q++) if (now - bw_t[q] <= 10 * (bbr_rt < 1e8 ? bbr_rt : 50) && bw_v[q] > mx) mx = bw_v[q];
                    if (mx > 0) bbr_bw = mx;
                    if (bbr_bw > 0 && bbr_rt < 1e8) {
                        if (bb_deliv >= bbr_round_end) {                                /* one packet-timed round elapsed */
                            bbr_round_end = bb_deliv + (long)(next - base > 1 ? next - base : 1);
                            if (bbr_state == 0) { if (bbr_bw >= bbr_full_bw * 1.25) { bbr_full_bw = bbr_bw; bbr_full_cnt = 0; } else if (++bbr_full_cnt >= 3) { bbr_state = 1; bbr_gain = 1.0 / 2.885; } }
                        }
                        double bdp = bbr_bw * bbr_rt / 1000.0;
                        if (bbr_state == 1 && (next - base) <= bdp) { bbr_state = 2; bbr_phase = 0; bbr_phase_start = now; bbr_gain = 1.0; }
                        if (bbr_state == 2 && now - bbr_phase_start >= bbr_rt) {
                            static const double pg[8] = { 1.25, 0.75, 1, 1, 1, 1, 1, 1 };
                            bbr_phase = (bbr_phase + 1) % 8; bbr_phase_start = now; bbr_gain = pg[bbr_phase];
                        }
                        cwnd = 2.0 * bdp; if (cwnd < 4) cwnd = 4; if (bbr_state == 0 && cwnd < 10) cwnd = 10;
                    }
                    if (cwnd > maxcwnd) cwnd = maxcwnd;
                }
                timer_on = base < npk; timer_start = now;
                if (tr) fprintf(tr, "%.1f,ACK,%.2f,%.1f\n", now - t0, cwnd, srtt);
            } else if (a == last_ack && a == pk[base].seq_num) {
                dup++;
                if (dup >= DUP_ACK_THRESHOLD && base > recover) {
                    recover = next - 1; fast_retx++; iv_lossev++; ss_phase = 0;
                    if ((!use_rl && !chat && !use_bbr) || guard > 0) {
                        if (use_cubic) { wmax = cwnd; ssthresh = cwnd * 0.7; if (ssthresh < 2) ssthresh = 2; cwnd = ssthresh; kcub = cbrt(wmax * 0.3 / 0.4); epoch = now; }
                        else { ssthresh = cwnd / 2; if (ssthresh < 1) ssthresh = 1; cwnd = ssthresh; }
                    }
                    { int hi = next; if (use_sack) { hi = base + 1; for (int i = base; i < next; i++) if (sacked[i]) hi = i; }
                      for (int i = base; i < hi; i++) if (!(use_sack && sacked[i]) && !(use_sack && rack_on && retx_flag[i] && now - sent_at[i] < (srtt > 0 ? srtt * 1.25 + 4 : 100))) TX(i, 1); }
                    dup = 0; timer_start = now;
                    if (tr) fprintf(tr, "%.1f,FAST_RETX,%.2f,%.1f\n", now - t0, cwnd, srtt);
                }
            } else last_ack = a;
        }
        /* 3b. SACK scoreboard loss detection (RFC 6675 / RACK style). A packet is lost when >= 3 packets sent after it are already SACKed;
               a retransmission is itself re-sent if it has been outstanding for more than ~1.25 x srtt and the scoreboard still says lost.
               All holes in a window are repaired in one round trip and the window is cut once per recovery episode. */
        if (use_sack && rack_on && base < next) {
            int cnt = 0, lost_found = 0;
            for (int i = next - 1; i >= base; i--) {
                if (sacked[i]) { cnt++; continue; }
                if (cnt >= 3 && (!retx_flag[i] || now - sent_at[i] >= (srtt > 0 ? srtt * 1.25 + 4 : 100))) { TX(i, 1); lost_found = 1; }
            }
            if (lost_found && base > recover) {
                recover = next - 1; fast_retx++; iv_lossev++; ss_phase = 0;
                if ((!use_rl && !chat && !use_bbr) || guard > 0) {
                    if (use_cubic) { wmax = cwnd; ssthresh = cwnd * 0.7; if (ssthresh < 2) ssthresh = 2; cwnd = ssthresh; kcub = cbrt(wmax * 0.3 / 0.4); epoch = now; }
                    else { ssthresh = cwnd / 2; if (ssthresh < 1) ssthresh = 1; cwnd = ssthresh; }
                }
                if (tr) fprintf(tr, "%.1f,SACK_RETX,%.2f,%.1f\n", now - t0, cwnd, srtt);
            }
            /* tail-loss probe: nothing has advanced for 2 srtt + 10 ms, so re-send the oldest unSACKed packet once without cutting the window */
            if (!tlp_sent && srtt > 0 && now - last_prog >= 2 * srtt + 10) {
                for (int i = base; i < next; i++) if (!sacked[i]) { if (!retx_flag[i] || now - sent_at[i] >= srtt * 1.25 + 4) TX(i, 1); break; }
                tlp_sent = 1;
            }
        }
        /* 4. RTO */
        int rto_fire = timer_on && base < npk && now - timer_start >= rto; int first_exp = base;
        if (use_sack && base < npk) {           /* Selective Repeat: every packet has its own timer */
            rto_fire = 0;
            for (int i = base; i < next; i++) if (!sacked[i] && now - sent_at[i] >= rto) { rto_fire = 1; first_exp = i; break; }
        }
        if (rto_fire) {
            double rto_old = rto;
            /* penalise once per loss event; in Selective Repeat several packets can expire inside one event */
            if (!use_sack || first_exp > recover) {
                timeouts++; iv_lossev++; ss_phase = 0;
                if (use_cubic) { wmax = cwnd; ssthresh = cwnd * 0.7; kcub = cbrt(wmax * 0.3 / 0.4); epoch = now; } else ssthresh = cwnd / 2;
                if (ssthresh < 2) ssthresh = 2;
                if (chat && guard > 0) { cwnd = 1.0; /* hybrid: AIMD is in control */ } else if (chat) { /* the chat-style agents leave the window to the policy; --tcut X adds an AIMD-style safety net: window x X on a timeout */ if (tcut > 0) { cwnd *= tcut; if (cwnd < 2) cwnd = 2; } } else if (use_rl) { cwnd = cwnd / 2 < 2 ? 2 : cwnd / 2; } else if (use_bbr) { /* BBR does not react to loss */ } else { cwnd = 1.0; }
                rto = rto * 2 > 3000 ? 3000 : rto * 2;
                recover = next - 1;
            }
            dup = 0;
            if (use_sack) { /* resend only the packets whose own timer has expired and that the receiver has not reported */
                for (int i = base; i < next; i++) if (!sacked[i] && now - sent_at[i] >= rto_old) TX(i, 1);
            } else for (int i = base; i < next; i++) TX(i, 1);
            timer_start = now;
            if (tr) fprintf(tr, "%.1f,TIMEOUT,%.2f,%.1f\n", now - t0, cwnd, srtt);
        }
        /* 5a. chat-style agents: decide every clamp(srtt, 100, 500) ms, as the trained agents expect */
        if (chat) {
            double ivlen = srtt > 0 ? (srtt < 100 ? 100 : srtt > 500 ? 500 : srtt) : 500;
            if (now - iv_start >= ivlen) {
                double dur = (now - iv_start) / 1000.0;
                long nw = iv_new, ac = iv_acked, le = iv_lossev;
                double avg_rtt = iv_rtt_n ? iv_rtt_sum / iv_rtt_n : (srtt > 0 ? srtt : 1);
                double sm_x[SM_IN], sm_aux[4]; int sm_a_ok = 0;
                if (use_smart) {
                    smiv.dur_ms = now - iv_start; smiv.srtt = srtt > 0 ? srtt : 0; smiv.min_rtt = min_rtt; smiv.rtt_sum = iv_rtt_sum; smiv.rtt_n = iv_rtt_n;
                    smiv.inflight = (double)(next - base); smiv.new_sent = nw; smiv.acked = ac; smiv.lossev = le;
                    sm_features(&smh, &smiv, cwnd, sm_x, sm_aux); sm_a_ok = 1; memset(smiv.bins, 0, sizeof smiv.bins);
                }
                iv_start = now; iv_rtt_sum = 0; iv_rtt_n = iv_acked = iv_sent = iv_retx = 0; iv_new = iv_lossev = 0;
                if (!(nw == 0 && ac == 0 && le == 0)) {
                    double base_r = min_rtt < 1e8 ? min_rtt : 50.0;
                    double ratio = avg_rtt / base_r; if (ratio < 1) ratio = 1;
                    double lossf = (double)le / (nw > 0 ? nw : 1); if (lossf > 1) lossf = 1;
                    double bs = base_r / 1000.0; if (bs < 0.02) bs = 0.02;
                    double peak = 32.0 * dur / bs; if (peak < 1) peak = 1;
                    double thr = ac / peak; if (thr > 1) thr = 1;
                    if (hyb > 0 && use_deep) { if (lossf >= hyb) guard = 3; else if (guard > 0) guard--; }
                    int lo = (lossf == 0 && ratio < 1.5) ? 2 : 0;           /* no congestion signal: never shrink */
                    int act = 2;
                    if (guard > 0) { if (tr) fprintf(tr, "%.1f,GUARD,%.2f,%.1f\n", now - t0, cwnd, srtt); }
                    else if (ss_phase) { if (ratio >= ss_ratio || lossf > 0) ss_phase = 0; }
                    else if (use_smart && sm_a_ok) {
                        double a = sm_forward(&smnet, sm_x);              /* mean action in (-1, 1) */
                        smh.last_a = a;
                        cwnd *= exp2(a); if (cwnd < 2) cwnd = 2; if (cwnd > maxcwnd) cwnd = maxcwnd;
                        if (tr) fprintf(tr, "%.1f,SMART,%.2f,%.1f\n", now - t0, cwnd, srtt);
                        goto smart_done;
                    }
                    else if (use_deep) {
                        double x[DN_IN] = { (ratio - 1.0 > 3.0 ? 3.0 : ratio - 1.0) / 3.0, (lossf > 0.3 ? 0.3 : lossf) / 0.3, cwnd / 32.0, thr, RL_MULT[last_act] - 1.0 }, q[DN_OUT];
                        { static double nz = -1; static int tr_ok = -1; static FILE *slog = NULL; static int slog_t = 0;
                          if (nz < 0) { nz = getenv("REPLIC_NOISE") ? atof(getenv("REPLIC_NOISE")) : 0; }
                          if (tr_ok < 0) { tr_ok = getenv("REPLIC_TREE") ? (tree_load(getenv("REPLIC_TREE")) == 0) : 0; }
                          if (!slog_t) { slog_t = 1; if (getenv("REPLIC_STATELOG")) slog = fopen(getenv("REPLIC_STATELOG"), "a"); }
                          if (nz > 0) for (int k = 0; k < 4; k++) x[k] += nz * gauss();
                          if (tr_ok) { act = tree_predict(x); if (act < lo) act = lo; }
                          else { deep_forward(&dnet, x, q); act = lo; for (int k = lo + 1; k < RL_NA; k++) if (q[k] > q[act]) act = k; }
                          if (slog) fprintf(slog, "%.5f,%.5f,%.5f,%.5f,%.5f,%d,%d\n", x[0], x[1], x[2], x[3], x[4], lo, act); }
                        if (polite > 0 && ratio >= polite && (avg_rtt - base_r) > 2.0 * rttvar && cwnd > 12 && act > 1) act = 1;
                    } else {
                        int s2 = rl_state(ratio, lossf, cwnd); act = lo; for (int k = lo + 1; k < RL_NA; k++) if (ag.q[s2][k] > ag.q[s2][act]) act = k;
                    }
                    if (guard == 0 && !ss_phase) { cwnd *= RL_MULT[act]; if (cwnd < 2) cwnd = 2; if (cwnd > maxcwnd) cwnd = maxcwnd; last_act = act; }
                    if (tr) fprintf(tr, "%.1f,RL_%d,%.2f,%.1f\n", now - t0, act, cwnd, srtt);
                    smart_done: ;
                }
            }
        }
        /* 5. RL monitor interval */
        if (use_rl) {
            double ivlen = srtt > 0 ? (srtt < 10 ? 10 : srtt) : 30;
            if (now - iv_start >= ivlen) {
                double secs = (now - iv_start) / 1000.0;
                double thr = iv_acked / secs;
                double avg_rtt = iv_rtt_n ? iv_rtt_sum / iv_rtt_n : (srtt > 0 ? srtt : 1);
                double base_rtt = min_rtt + 2 * (dev_floor < 1e8 ? dev_floor : 0);
                double ratio = min_rtt < 1e8 ? avg_rtt / base_rtt : 1.0;
                double lossf = iv_sent ? (double)iv_retx / iv_sent : 0.0;
                if (lossf > 1) lossf = 1;
                double tn = thr / ref; if (tn > 1) tn = 1;
                double reward = sqrt(tn) - 0.5 * (ratio > 1 ? ratio - 1 : 0) - 4.0 * lossf;
                if (reward > 1.5) reward = 1.5;
                int s2 = rl_state(ratio, lossf, cwnd);
                if (prev_s >= 0) {
                    if (train) rl_update(&ag, prev_s, prev_a, reward, s2);
                    rew_sum += reward; rew_n++;
                }
                int act = rl_choose(&ag, s2);
                double nc = cwnd * RL_MULT[act];
                if (RL_MULT[act] > 1.0 && nc < cwnd + 1) nc = cwnd + 1;
                if (nc < 1) { nc = 1; }
                if (nc > maxcwnd) { nc = maxcwnd; }
                cwnd = nc; prev_s = s2; prev_a = act;
                iv_start = now; iv_rtt_sum = 0; iv_rtt_n = iv_acked = iv_sent = iv_retx = 0;
                if (tr) fprintf(tr, "%.1f,RL_%d,%.2f,%.1f\n", now - t0, act, cwnd, srtt);
            }
        }
        /* 6. send within window */
        while (next < npk && next < base + (int)cwnd) {
            if (use_bbr && bbr_bw > 0) {                 /* BBR paces at gain x estimated bottleneck bandwidth */
                if (pace_next < now - 2.0) pace_next = now - 2.0;
                if (pace_next > now) break;
                pace_next += 1000.0 / (bbr_gain * bbr_bw);
            } else if (pace > 0 && srtt > 0) {                  /* --pace G: spread new packets over the round trip at G x cwnd/srtt (like fq pacing) */
                if (pace_next < now - 2.0) pace_next = now - 2.0;   /* at most ~2 ms of accumulated credit */
                if (pace_next > now) break;
                pace_next += srtt / (cwnd * pace);
            }
            TX(next, 0); if (!timer_on) { timer_on = 1; timer_start = now_ms(); } next++;
        }
        /* 7. wait a little for the next event */
        if (vt) vclock += vstep;
        else { struct pollfd pf = { sockfd, POLLIN, 0 }; poll(&pf, 1, 0);
        struct timespec ts = { 0, 300000 }; nanosleep(&ts, NULL); }
        if (now_ms() - t0 > 600000) { fprintf(stderr, "abort: stuck\n"); return REPLIC_ERR_STUCK; }
    }
    double done = now_ms() - t0;
    if (tr) fprintf(tr, "%.1f,DONE,%.2f,%.1f\n", done, cwnd, srtt);

    /* teardown */
    replic_packet_t fin; memset(&fin, 0, sizeof fin);
    fin.seq_num = base_seq + (uint32_t)npk; fin.flags = FLAG_FIN;
    int l = replic_pack(&fin, buf, sizeof buf);
    if (!vt) {
    /* v2 fix: the FIN used to be sent once; if it was lost the server never closed (and never flushed its output file).
       Retransmit it until the matching ACK arrives, at most 12 tries, 150 ms apart. */
    uint8_t fb[BUFFER_SIZE]; memcpy(fb, buf, (size_t)l);
    for (int tryn = 0; tryn < 12; tryn++) {
        sendto(sockfd, fb, (size_t)l, 0, (struct sockaddr *)&sa, sizeof sa);
        double tend = now_ms() + 150; int got = 0;
        while (now_ms() < tend && !got) {
            struct pollfd pf = { sockfd, POLLIN, 0 };
            if (poll(&pf, 1, 20) <= 0) continue;
            uint8_t rb[BUFFER_SIZE]; ssize_t rn = recvfrom(sockfd, rb, sizeof rb, 0, NULL, NULL);
            replic_packet_t rp; if (rn > 0 && replic_unpack(rb, (size_t)rn, &rp) == 0 && (rp.flags & FLAG_ACK) && rp.ack_num == fin.seq_num + 1) got = 1;
        }
        if (got) break;
    } }

    if (use_rl && train && qfile) rl_save(&ag, qfile);
    if (res) {
        memset(res, 0, sizeof *res);
        res->elapsed_ms = done; res->packets = npk; res->bytes = (ff || data || fpath) ? fbytes : (long)npk * PKT_BYTES; res->fnv1a = (fpath || data) ? ffnv : 0;
        res->transmissions = tx_total; res->retransmissions = retx; res->timeouts = timeouts; res->fast_retransmits = fast_retx;
        res->avg_rtt_ms = rtt_n ? rtt_sum / rtt_n : 0.0; res->min_rtt_ms = min_rtt < 1e8 ? min_rtt : 0.0; res->goodput_pps = npk / (done / 1000.0);
        res->final_cwnd = cwnd; res->avg_reward = rew_n ? rew_sum / rew_n : 0.0;
        res->share_ours = ours_ok; res->share_comp = comp_ok; res->link_share = (vt && comp_on && ours_ok + comp_ok) ? (double)ours_ok / (ours_ok + comp_ok) : -1.0;
    }
    if (tr) fclose(tr);
    if (pcapf) { fclose(pcapf); pcapf = NULL; }
    if (!vt) close(sockfd);
    free(fwd.a); free(rev.a);
    return REPLIC_OK;
}

/* ------------------------------------------------------------------ public API ------------------------------------------------------------------ */
void replic_options_default(replic_options_t *o) {
    memset(o, 0, sizeof *o);
    o->host = "127.0.0.1"; o->port = SERVER_PORT; o->mode = "aimd"; o->init_cwnd = -1; o->ssthresh = 16;
    o->slow_start_to = -1; o->slow_start_ratio = 1.3; o->hybrid = -1; o->delay_ms = 10; o->queue_packets = 50;
    o->seed = 1; o->packets = 1500; o->scenario = "custom"; o->vstep_ms = 0.35;
}
int replic_send(const replic_options_t *o, const void *data, size_t len, replic_stats_t *stats) {
    if (!o || !data) return REPLIC_ERR_ARGS;
    return run_transfer(o, NULL, data, len, stats);
}
int replic_send_file(const replic_options_t *o, const char *path, replic_stats_t *stats) {
    if (!o || !path) return REPLIC_ERR_ARGS;
    return run_transfer(o, path, NULL, 0, stats);
}
int replic_send_test_pattern(const replic_options_t *o, replic_stats_t *stats) { return o ? run_transfer(o, NULL, NULL, 0, stats) : REPLIC_ERR_ARGS; }
const char *replic_strerror(int code) {
    switch (code) { case REPLIC_OK: return "ok"; case REPLIC_ERR_IO: return "I/O error (socket, handshake or file)"; case REPLIC_ERR_ARGS: return "invalid arguments"; case REPLIC_ERR_STUCK: return "transfer stuck (no progress for 10 minutes)"; default: return "unknown error"; }
}
