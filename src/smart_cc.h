/*
 * smart_cc.h - "Poise" congestion controller: state features + continuous-action policy.
 *
 * Shared by THREE programs so that training and deployment can never disagree about what the agent sees:
 *   - src/rudp_sender.c      (--mode smart)          real UDP transfers
 *   - sim/netsim.c           (libnetsim.so)          the training simulator that train_smart_rudp.py drives
 *   - Studio / tests         (via the sender)
 *
 * State vector s_t (SM_IN = 8), all values scaled to about [-1, 1] or [0, 1]:
 *   x0  smoothed-RTT gradient        (SRTT_t - SRTT_{t-1}) / minRTT                    clipped to [-1, 1]
 *   x1  queue-delay trend            EWMA of  qd_t - qd_{t-1},  qd = (avgRTT - minRTT)/minRTT   clipped to [-1, 1]
 *   x2  queue-delay level            qd / 3                                             clipped to [0, 1]
 *   x3  loss ratio, last window      loss events / new packets sent in the interval / 0.3        [0, 1]
 *   x4  in-flight / BDP              in-flight packets / (max delivery rate over last 10 intervals * minRTT), / 4  [0, 1]
 *   x5  competing-traffic pressure   Fano factor (variance/mean) of ACK counts in 10 ms bins over the interval, / 6  [0, 1]
 *                                    (cross traffic interleaves with our packets at the bottleneck and makes our ACK
 *                                     stream burstier than a lone paced flow's)
 *   x6  previous action              a_{t-1} in [-1, 1]
 *   x7  window size                  log2(cwnd) / 7   (cwnd in 2..128 -> 0.14..1)
 *       x7 is what lets a multiplicative-factor policy realise an ADDITIVE increase (C = 1 + k/cwnd) and so converge to fair
 *       shares like AIMD does; a first version without it could not learn fairness (see results/smart/ablation_nocwnd_*).
 * Action: a_t in [-1, 1],  C_t = 2^{a_t}  in [0.5, 2.0],  cwnd_{t+1} = clamp(cwnd_t * C_t, 2, cwnd_max).
 * Policy: tanh MLP 7 -> 32 -> 32 -> 1 (1,377 weights); tanh(output) is the mean of the training-time Gaussian policy and is
 * used deterministically at run time.
 */
#ifndef SMART_CC_H
#define SMART_CC_H
#include <math.h>
#include <stdio.h>
#include <string.h>

#define SM_IN 8
#define SM_H 32
#define SM_NBINS 64
#define SM_BIN_MS 10.0
#define SM_NDR 10

typedef struct {
    float W1[SM_H][SM_IN], b1[SM_H], W2[SM_H][SM_H], b2[SM_H], W3[SM_H], b3;
} sm_net_t;

/* counters of one control interval; the sender and the simulator fill the same fields */
typedef struct {
    double dur_ms, srtt, min_rtt, rtt_sum, inflight;
    long rtt_n, new_sent, acked, lossev;
    int bins[SM_NBINS];
} sm_iv_t;

/* per-flow memory between intervals */
typedef struct {
    double prev_srtt, prev_qd, trend, dr[SM_NDR], last_a;
    int ndr, idr, have;
} sm_hist_t;

static inline void sm_hist_init(sm_hist_t *h) { memset(h, 0, sizeof *h); }
static inline double sm_clip(double v, double lo, double hi) { return v < lo ? lo : v > hi ? hi : v; }

/* Computes x[SM_IN] from one interval and updates the history. aux (optional, 4 values): qd, avg_rtt, lossf, delivery rate (pkt/s). */
static inline void sm_features(sm_hist_t *h, const sm_iv_t *iv, double cwnd, double x[SM_IN], double *aux) {
    double avg = iv->rtt_n > 0 ? iv->rtt_sum / iv->rtt_n : (iv->srtt > 0 ? iv->srtt : 1.0);
    double mn = iv->min_rtt > 0 && iv->min_rtt < 1e8 ? iv->min_rtt : avg;
    if (mn < 1.0) mn = 1.0;
    double qd = (avg - mn) / mn; if (qd < 0) qd = 0;
    double grad = h->have ? (iv->srtt - h->prev_srtt) / mn : 0.0;
    double tr = h->have ? qd - h->prev_qd : 0.0;
    h->trend = 0.5 * h->trend + 0.5 * tr;
    double lossf = iv->new_sent > 0 ? (double)iv->lossev / (double)iv->new_sent : (iv->lossev > 0 ? 1.0 : 0.0);
    double dur = iv->dur_ms > 1.0 ? iv->dur_ms : 1.0;
    double dr = (double)iv->acked * 1000.0 / dur;
    h->dr[h->idr] = dr; h->idr = (h->idr + 1) % SM_NDR; if (h->ndr < SM_NDR) h->ndr++;
    double mx = 0; for (int i = 0; i < h->ndr; i++) if (h->dr[i] > mx) mx = h->dr[i];
    double bdp = mx * mn / 1000.0; if (bdp < 2.0) bdp = 2.0;
    int nb = (int)(dur / SM_BIN_MS); if (nb > SM_NBINS) nb = SM_NBINS;
    double fano = 0;
    if (nb >= 3) {
        double s = 0, q = 0; for (int i = 0; i < nb; i++) { s += iv->bins[i]; q += (double)iv->bins[i] * iv->bins[i]; }
        double m = s / nb; if (m > 0) { double var = q / nb - m * m; fano = (var > 0 ? var : 0) / m; }
    }
    x[0] = sm_clip(grad, -1, 1);
    x[1] = sm_clip(h->trend, -1, 1);
    x[2] = sm_clip(qd / 3.0, 0, 1);
    x[3] = sm_clip(lossf / 0.3, 0, 1);
    x[4] = sm_clip(iv->inflight / bdp / 4.0, 0, 1);
    x[5] = sm_clip(fano / 6.0, 0, 1);
    x[6] = h->last_a;
    x[7] = sm_clip(log2(cwnd < 2 ? 2 : cwnd) / 7.0, 0, 1);
    h->prev_srtt = iv->srtt; h->prev_qd = qd; h->have = 1;
    if (aux) { aux[0] = qd; aux[1] = avg; aux[2] = lossf; aux[3] = dr; }
}

/* forward pass: returns the mean action tanh(output) in (-1, 1) */
static inline double sm_forward(const sm_net_t *n, const double x[SM_IN]) {
    double h1[SM_H], h2[SM_H];
    for (int i = 0; i < SM_H; i++) { double s = n->b1[i]; for (int j = 0; j < SM_IN; j++) s += n->W1[i][j] * x[j]; h1[i] = tanh(s); }
    for (int i = 0; i < SM_H; i++) { double s = n->b2[i]; for (int j = 0; j < SM_H; j++) s += n->W2[i][j] * h1[j]; h2[i] = tanh(s); }
    double o = n->b3; for (int i = 0; i < SM_H; i++) o += n->W3[i] * h2[i];
    return tanh(o);
}

/* runtime loader for a text weight file written by train_smart_rudp.py:  "SMART1 7 32" then W1,b1,W2,b2,W3,b3 as floats */
static inline int sm_load(sm_net_t *n, const char *path) {
    FILE *f = fopen(path, "r"); if (!f) return -1;
    char tag[16]; int a, b; if (fscanf(f, "%15s %d %d", tag, &a, &b) != 3 || strcmp(tag, "SMART1") || a != SM_IN || b != SM_H) { fclose(f); return -2; }
    int ok = 1; float v;
#define RD(p) do { if (fscanf(f, "%f", &v) != 1) ok = 0; else (p) = v; } while (0)
    for (int i = 0; i < SM_H; i++) for (int j = 0; j < SM_IN; j++) RD(n->W1[i][j]);
    for (int i = 0; i < SM_H; i++) RD(n->b1[i]);
    for (int i = 0; i < SM_H; i++) for (int j = 0; j < SM_H; j++) RD(n->W2[i][j]);
    for (int i = 0; i < SM_H; i++) RD(n->b2[i]);
    for (int i = 0; i < SM_H; i++) RD(n->W3[i]);
    RD(n->b3);
#undef RD
    fclose(f); return ok ? 0 : -3;
}

/* compiled-in weights: neural_weights.h (generated by train_smart_rudp.py --export) defines SM_BUILTIN and the arrays below */
#ifdef SM_HAVE_BUILTIN
#include "neural_weights.h"
static inline void sm_builtin(sm_net_t *n) {
    memcpy(n->W1, SM_W1, sizeof n->W1); memcpy(n->b1, SM_B1, sizeof n->b1); memcpy(n->W2, SM_W2, sizeof n->W2);
    memcpy(n->b2, SM_B2, sizeof n->b2); memcpy(n->W3, SM_W3, sizeof n->W3); n->b3 = SM_B3;
}
#endif
#endif
