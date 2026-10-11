/* impair: a small user-space network impairment proxy for UDP (a stand-in for tc/netem when no root or kernel module is
 * available). It sits between clients and servers on real sockets and models ONE shared bottleneck:
 *
 *     client A --UDP--> impair :LISTEN_A --UDP--> server A :TARGET_A     (flows share the forward bottleneck)
 *     client B --UDP--> impair :LISTEN_B --UDP--> server B :TARGET_B
 *
 * Forward direction: random loss, one-way delay + jitter (kept in order per flow), a bottleneck of RATE packets/s shared
 * by all flows with a drop-tail queue of QUEUE packets. Reverse direction: delay + jitter only.
 *   usage: impair LISTEN:TARGET [LISTEN:TARGET ...] [--delay ms] [--jitter ms] [--loss pct] [--rate pps] [--queue n] [--seed n]
 *   (for compatibility "LISTEN TARGET" as two separate numbers also works for a single flow)
 * When the first flow sends its FIN, the number of packets each flow got through the bottleneck so far is printed
 * ("SHARE ..."), which is what the fairness test reads. Stop with SIGTERM: totals go to stderr.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <math.h>
#include <poll.h>
#include <unistd.h>
#include <signal.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#define RING 8192
#define MAXF 8
typedef struct { double due; int len; int flow; uint8_t d[1500]; } item_t;
typedef struct { item_t *q; int h, t; double last_due; } lane_t;
typedef struct { int in, out; struct sockaddr_in srv, cli; int have_cli; long fwd; lane_t rv; double last_due_f; } flow_t;

static volatile sig_atomic_t stop;
static void on_term(int sig) { (void)sig; stop = 1; }
static double now_ms(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6; }
static double urand(void) { return rand() / (RAND_MAX + 1.0); }

int main(int argc, char **argv) {
    flow_t fl[MAXF]; int nf = 0, queue = 50; double delay = 10, jitter = 0, loss = 0, rate = 0, xdelay = 0; unsigned seed = 1;   /* --xdelay ms: extra ACK-path delay for the second flow (unequal RTTs) */
    double x_on = 0, x_off = 0, x_pps = 0;   /* --cross ON_MS:OFF_MS:PPS bursty cross traffic that shares the bottleneck (exponential on/off periods) */
    int pend_num = -1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--cross") && i + 1 < argc) { sscanf(argv[++i], "%lf:%lf:%lf", &x_on, &x_off, &x_pps); }
        else if (!strncmp(argv[i], "--", 2) && i + 1 < argc) {
            double v = atof(argv[++i]);
            if (!strcmp(argv[i - 1], "--delay")) delay = v; else if (!strcmp(argv[i - 1], "--jitter")) jitter = v;
            else if (!strcmp(argv[i - 1], "--loss")) loss = v; else if (!strcmp(argv[i - 1], "--rate")) rate = v;
            else if (!strcmp(argv[i - 1], "--queue")) queue = (int)v; else if (!strcmp(argv[i - 1], "--seed")) seed = (unsigned)v; else if (!strcmp(argv[i - 1], "--xdelay")) xdelay = v;
        } else if (nf < MAXF) {
            int l, t;
            if (sscanf(argv[i], "%d:%d", &l, &t) == 2) { memset(&fl[nf], 0, sizeof fl[nf]); fl[nf].in = l; fl[nf].out = t; nf++; }
            else if (pend_num < 0) pend_num = atoi(argv[i]);
            else { memset(&fl[nf], 0, sizeof fl[nf]); fl[nf].in = pend_num; fl[nf].out = atoi(argv[i]); nf++; pend_num = -1; }
        }
    }
    if (nf == 0) { fprintf(stderr, "usage: %s LISTEN:TARGET [LISTEN:TARGET ...] [--delay ms] [--jitter ms] [--loss pct] [--rate pps] [--queue n] [--seed n]\n", argv[0]); return 1; }
    srand(seed); signal(SIGTERM, on_term); signal(SIGINT, on_term);
    struct pollfd p[2 * MAXF];
    for (int f = 0; f < nf; f++) {
        int lp = fl[f].in, tp = fl[f].out;
        fl[f].in = socket(AF_INET, SOCK_DGRAM, 0); fl[f].out = socket(AF_INET, SOCK_DGRAM, 0);
        struct sockaddr_in a = {0}; a.sin_family = AF_INET; a.sin_addr.s_addr = inet_addr("127.0.0.1"); a.sin_port = htons((uint16_t)lp);
        if (bind(fl[f].in, (struct sockaddr *)&a, sizeof a) < 0) { perror("bind"); return 1; }
        fl[f].srv = a; fl[f].srv.sin_port = htons((uint16_t)tp);
        fl[f].rv.q = calloc(RING, sizeof(item_t));
        p[2 * f] = (struct pollfd){ fl[f].in, POLLIN, 0 }; p[2 * f + 1] = (struct pollfd){ fl[f].out, POLLIN, 0 };
    }
    lane_t fw = { calloc(RING, sizeof(item_t)), 0, 0, 0 }; double last_dep = 0, last_due_f = 0;
    long drops_rand = 0, drops_queue = 0; int shown = 0;
    double x_phase_end = now_ms() + (x_off > 0 ? -x_off * log(1 - urand()) : 0), x_next = 0; int x_is_on = 0; long x_sent = 0;
    while (!stop) {
        double now = now_ms(), next = now + 50;
        if (fw.h < fw.t && fw.q[fw.h % RING].due < next) next = fw.q[fw.h % RING].due;
        for (int f = 0; f < nf; f++) if (fl[f].rv.h < fl[f].rv.t && fl[f].rv.q[fl[f].rv.h % RING].due < next) next = fl[f].rv.q[fl[f].rv.h % RING].due;
        if (x_pps > 0) {
            while (now >= x_phase_end) { x_is_on = !x_is_on; x_phase_end = now + -(x_is_on ? x_on : x_off) * log(1 - urand()); x_next = now; }
            if (x_is_on) { while (x_next <= now) { if (rate > 0) { if (last_dep - now <= queue * 1000.0 / rate) { last_dep = (last_dep > now ? last_dep : now) + 1000.0 / rate; x_sent++; } } x_next += 1000.0 / x_pps; } if (x_next < next) next = x_next; }
            else if (x_phase_end < next) next = x_phase_end;
        }
        int to = (int)(next - now); if (to < 0) to = 0;
        if (poll(p, (nfds_t)(2 * nf), to) > 0) {
            for (int k = 0; k < 2 * nf; k++) if (p[k].revents & POLLIN) {
                int f = k / 2, fwd_dir = (k % 2 == 0);
                uint8_t buf[1500]; struct sockaddr_in from; socklen_t fln = sizeof from;
                int n = (int)recvfrom(p[k].fd, buf, sizeof buf, 0, (struct sockaddr *)&from, &fln); if (n <= 0) continue;
                double t = now_ms();
                if (fwd_dir) {
                    fl[f].cli = from; fl[f].have_cli = 1;
                    if (n >= 13 && (buf[8] & 0x04) && !shown) { shown = 1; printf("SHARE"); for (int g = 0; g < nf; g++) printf(" %ld", fl[g].fwd); printf("\n"); fflush(stdout); }
                    if (loss > 0 && urand() * 100.0 < loss) { drops_rand++; continue; }
                    double dep = t;
                    if (rate > 0) {
                        if (last_dep - t > queue * 1000.0 / rate) { drops_queue++; continue; }
                        dep = (last_dep > t ? last_dep : t) + 1000.0 / rate; last_dep = dep;
                    }
                    double due = dep + delay + (jitter > 0 ? (urand() * 2 - 1) * jitter : 0);
                    if (due < last_due_f) due = last_due_f;
                    last_due_f = due;
                    if (fw.t - fw.h >= RING) continue;
                    item_t *it = &fw.q[fw.t % RING]; it->due = due; it->len = n; it->flow = f; memcpy(it->d, buf, (size_t)n); fw.t++;
                    fl[f].fwd++;
                } else {
                    lane_t *ln = &fl[f].rv;
                    double due = t + delay + (f == 1 ? xdelay : 0) + (jitter > 0 ? (urand() * 2 - 1) * jitter : 0); if (due < ln->last_due) due = ln->last_due; ln->last_due = due;
                    if (ln->t - ln->h >= RING) continue;
                    item_t *it = &ln->q[ln->t % RING]; it->due = due; it->len = n; it->flow = f; memcpy(it->d, buf, (size_t)n); ln->t++;
                }
            }
        }
        double t = now_ms();
        /* the shared queue is FIFO in departure order, but each item has its own due time; release the head while due */
        while (fw.h < fw.t && fw.q[fw.h % RING].due <= t) { item_t *it = &fw.q[fw.h++ % RING]; sendto(fl[it->flow].out, it->d, (size_t)it->len, 0, (struct sockaddr *)&fl[it->flow].srv, sizeof fl[it->flow].srv); }
        for (int f = 0; f < nf; f++) { lane_t *ln = &fl[f].rv; while (ln->h < ln->t && ln->q[ln->h % RING].due <= t && fl[f].have_cli) { item_t *it = &ln->q[ln->h++ % RING]; sendto(fl[f].in, it->d, (size_t)it->len, 0, (struct sockaddr *)&fl[f].cli, sizeof fl[f].cli); } }
    }
    fprintf(stderr, "impair: random drops %ld, queue drops %ld; per-flow forwarded", drops_rand, drops_queue);
    for (int f = 0; f < nf; f++) fprintf(stderr, " %ld", fl[f].fwd);
    fprintf(stderr, "\n"); return 0;
}
