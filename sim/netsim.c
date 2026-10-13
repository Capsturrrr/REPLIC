/*
 * netsim.c - packet-level dumbbell simulator used to TRAIN the Poise agent (driven from Python via ctypes).
 *
 * One bottleneck (service rate mu pkt/s, drop-tail buffer, forward random loss), base round-trip delay, ACK jitter,
 * 1 ms ticks. Up to 8 flows share the bottleneck. Flow types:
 *   0 learned   window set from outside once per control interval; classic slow start (+1 per ACK) until ss=0; paced at 2*cwnd/srtt
 *   1 aimd      slow start to ssthresh, +1/cwnd per ACK, x0.5 per loss event       (window-limited, unpaced)
 *   2 cubic     CUBIC growth (C=0.4), x0.7 per loss event                          (window-limited, unpaced)
 *   3 bbr       simplified BBRv1: startup, drain, 8-phase gain cycle, cwnd = 2*BDP, paced at gain*btlbw
 * Losses are noticed one RTT after the drop; one "loss event" is counted per RTT like the real sender's recovery rule.
 * Interval features come from src/smart_cc.h, the SAME code the C sender runs.
 * Build:  gcc -O2 -shared -fPIC -o sim/libnetsim.so sim/netsim.c -lm
 */
#include <stdint.h>
#include <stdlib.h>
#include "../src/smart_cc.h"

#define MAXF 8
#define QMAX 8192
#define WHEEL 16384
#define EVMAX (1 << 20)

typedef struct { int flow; double sent_at; } qpkt_t;
typedef struct { int32_t next; int16_t flow, kind; double sent_at; } ev_t;

typedef struct {
    int type, ss, nstarted; double start_ms, ss_to;
    double cwnd, ssthresh, maxcwnd, inflight, retx_pending;
    double srtt, rttvar, min_rtt, pace_credit, recover_until, wmax, kc, epoch;
    double cwgain, pscale, bw, gain, win_start, bwh[10], phase_start, full_bw; long win_acked; int bwn, state, phase, full_cnt;
    long acked_total, lost_total, sent_total;
    sm_iv_t iv; double iv_start; sm_hist_t hist;
} flow_t;

typedef struct {
    double now, mu, base_rtt, loss_p, jitter, credit;
    int buf, nf, qh, qt, qlen, freeev; uint64_t rng;
    qpkt_t q[QMAX]; int32_t wheel[WHEEL]; ev_t *ev; flow_t f[MAXF];
} sim_t;

static double rnd(sim_t *s) { s->rng ^= s->rng << 13; s->rng ^= s->rng >> 7; s->rng ^= s->rng << 17; return (double)(s->rng >> 11) / 9007199254740992.0; }

void *sim_new(double mu, double base_rtt, int buf, double loss_pct, double jitter, unsigned seed) {
    sim_t *s = calloc(1, sizeof *s); s->ev = malloc(sizeof(ev_t) * EVMAX);
    s->mu = mu; s->base_rtt = base_rtt; s->buf = buf > QMAX - 1 ? QMAX - 1 : buf; s->loss_p = loss_pct / 100.0; s->jitter = jitter;
    s->rng = 88172645463325252ULL ^ ((uint64_t)seed * 0x9E3779B97F4A7C15ULL); for (int i = 0; i < 8; i++) rnd(s);
    for (int i = 0; i < WHEEL; i++) s->wheel[i] = -1;
    for (int i = 0; i < EVMAX - 1; i++) s->ev[i].next = i + 1; s->ev[EVMAX - 1].next = -1; s->freeev = 0;
    return s;
}
void sim_free(void *p) { sim_t *s = p; free(s->ev); free(s); }
void sim_set_mu(void *p, double mu) { ((sim_t *)p)->mu = mu; }

int sim_add_flow(void *p, int type, double init_cwnd, double maxcwnd, double start_ms, double ssthresh, double ss_to) {
    sim_t *s = p; if (s->nf >= MAXF) return -1;
    flow_t *f = &s->f[s->nf]; memset(f, 0, sizeof *f);
    f->type = type; f->cwnd = init_cwnd; f->maxcwnd = maxcwnd; f->start_ms = start_ms; f->ssthresh = ssthresh;
    f->srtt = -1; f->min_rtt = 1e9; f->epoch = -1; f->ss = (type == 0); f->ss_to = ss_to; f->gain = 2.885; f->iv_start = start_ms; f->cwgain = (type == 3 && ss_to >= 1 && ss_to < 10) ? ss_to : 2.0; f->pscale = (type == 3 && ssthresh > 0 && ssthresh < 10) ? ssthresh : 1.0;
    sm_hist_init(&f->hist); f->iv.min_rtt = 1e9;
    return s->nf++;
}
void sim_set_cwnd(void *p, int i, double c) { sim_t *s = p; flow_t *f = &s->f[i]; f->cwnd = c < 2 ? 2 : c > f->maxcwnd ? f->maxcwnd : c; }
void sim_set_ss(void *p, int i, int v) { ((sim_t *)p)->f[i].ss = v; }
void sim_set_action(void *p, int i, double a) { ((sim_t *)p)->f[i].hist.last_a = a; }
void sim_state(void *p, int i, double *o) { sim_t *s = p; flow_t *f = &s->f[i]; o[0] = f->cwnd; o[1] = f->inflight; o[2] = f->srtt; o[3] = f->min_rtt; o[4] = (double)f->acked_total; o[5] = f->ss; o[6] = (double)f->lost_total; o[7] = s->now; }

static void sched(sim_t *s, double delay, int flow, int kind, double sent_at) {
    int d = (int)delay; if (d < 1) d = 1; if (d > 8000) d = 8000;
    if (s->freeev < 0) return;
    int e = s->freeev; s->freeev = s->ev[e].next;
    int slot = ((int)s->now + d) & (WHEEL - 1);
    s->ev[e].flow = flow; s->ev[e].kind = kind; s->ev[e].sent_at = sent_at; s->ev[e].next = s->wheel[slot]; s->wheel[slot] = e;
}

static void on_loss_event(sim_t *s, flow_t *f) {
    if (s->now < f->recover_until) return;
    f->iv.lossev++; f->recover_until = s->now + (f->srtt > 0 ? f->srtt : 100.0);
    if (f->type == 0) f->ss = 0;
    else if (f->type == 1) { f->ssthresh = f->cwnd / 2; if (f->ssthresh < 2) f->ssthresh = 2; f->cwnd = f->ssthresh; }
    else if (f->type == 2) { f->wmax = f->cwnd; f->ssthresh = f->cwnd * 0.7; if (f->ssthresh < 2) f->ssthresh = 2; f->cwnd = f->ssthresh; f->kc = cbrt(f->wmax * 0.3 / 0.4); f->epoch = s->now; }
}

static void on_ack(sim_t *s, flow_t *f, double sent_at) {
    double rtt = s->now - sent_at;
    f->inflight -= 1; if (f->inflight < 0) f->inflight = 0; f->acked_total++; f->iv.acked++; f->iv.rtt_sum += rtt; f->iv.rtt_n++;
    if (rtt < f->min_rtt) f->min_rtt = rtt;
    if (f->srtt < 0) { f->srtt = rtt; f->rttvar = rtt / 2; } else { f->rttvar = 0.75 * f->rttvar + 0.25 * fabs(f->srtt - rtt); f->srtt = 0.875 * f->srtt + 0.125 * rtt; }
    int bi = (int)((s->now - f->iv_start) / SM_BIN_MS); if (bi >= 0 && bi < SM_NBINS) f->iv.bins[bi]++;
    if (f->type == 0) { if (f->ss) { f->cwnd += 1; if (f->cwnd >= f->ss_to) f->ss = 0; if (f->cwnd > f->maxcwnd) f->cwnd = f->maxcwnd; } }
    else if (f->type == 1 || f->type == 2) {
        if (f->cwnd < f->ssthresh) f->cwnd += 1.0;
        else if (f->type == 2) {
            if (f->epoch < 0) { f->epoch = s->now; f->wmax = f->cwnd; f->kc = 0; }
            double tt = (s->now - f->epoch) / 1000.0, tg = 0.4 * pow(tt - f->kc, 3) + f->wmax;
            f->cwnd += tg > f->cwnd ? (tg - f->cwnd) / f->cwnd : 0.01 / f->cwnd;
        } else f->cwnd += 1.0 / f->cwnd;
        if (f->cwnd > f->maxcwnd) f->cwnd = f->maxcwnd;
    } else {                                             /* simplified BBR */
        f->win_acked++; double win = f->min_rtt < 1e8 ? (f->min_rtt < 5 ? 5 : f->min_rtt) : 20;
        if (f->win_start == 0) f->win_start = s->now;
        if (s->now - f->win_start >= win) {
            double sm = f->win_acked * 1000.0 / (s->now - f->win_start); f->bwh[f->bwn % 10] = sm; f->bwn++;
            double mx = 0; for (int i = 0; i < 10 && i < f->bwn; i++) if (f->bwh[i] > mx) mx = f->bwh[i];
            f->bw = mx; f->win_acked = 0; f->win_start = s->now;
            if (f->state == 0) { if (f->bw >= f->full_bw * 1.25) { f->full_bw = f->bw; f->full_cnt = 0; } else if (++f->full_cnt >= 3) { f->state = 1; f->gain = 1.0 / 2.885; } }
        }
        if (f->bw > 0 && f->min_rtt < 1e8) {
            double bdp = f->bw * f->min_rtt / 1000.0;
            if (f->state == 1 && f->inflight <= bdp) { f->state = 2; f->phase = 0; f->phase_start = s->now; f->gain = 1.0; }
            if (f->state == 2 && s->now - f->phase_start >= f->min_rtt) { static const double pg[8] = {1.25, 0.75, 1, 1, 1, 1, 1, 1}; f->phase = (f->phase + 1) % 8; f->phase_start = s->now; f->gain = pg[f->phase]; }
            f->cwnd = f->cwgain * bdp; if (f->cwnd < 4) f->cwnd = 4; if (f->state == 0 && f->cwnd < 10) f->cwnd = 10; if (f->cwnd > f->maxcwnd) f->cwnd = f->maxcwnd;
        }
    }
}

static void tick(sim_t *s) {
    s->now += 1.0; int slot = (int)s->now & (WHEEL - 1);
    for (int e = s->wheel[slot]; e >= 0;) {
        ev_t *ev = &s->ev[e]; int nx = ev->next; flow_t *f = &s->f[ev->flow];
        if (ev->kind == 0) on_ack(s, f, ev->sent_at);
        else { f->inflight -= 1; if (f->inflight < 0) f->inflight = 0; f->lost_total++; f->retx_pending += 1; on_loss_event(s, f); }
        ev->next = s->freeev; s->freeev = e; e = nx;
    }
    s->wheel[slot] = -1;
    /* senders */
    for (int k = 0; k < s->nf; k++) {
        flow_t *f = &s->f[(k + (int)s->now) % s->nf];
        if (s->now < f->start_ms) continue;
        double allowed = floor(f->cwnd) - f->inflight; if (allowed <= 0) continue;
        double rate = -1;
        if (f->type == 0 && f->srtt > 0) rate = 2.0 * f->cwnd / f->srtt * 1000.0;
        else if (f->type == 3 && f->bw > 0) rate = f->pscale * f->gain * f->bw;
        double n = allowed;
        if (rate > 0) { f->pace_credit += rate * 0.001; if (f->pace_credit > rate * 0.002 + 1) f->pace_credit = rate * 0.002 + 1; n = floor(f->pace_credit); if (n > allowed) n = allowed; f->pace_credit -= n; }
        if (n > 200) n = 200;
        for (int i = 0; i < (int)n; i++) {
            if (f->retx_pending >= 1) f->retx_pending -= 1; else { f->iv.new_sent++; f->sent_total++; }
            f->inflight += 1;
            double qdelay = s->mu > 0 ? s->qlen / s->mu * 1000.0 : 0;
            if (rnd(s) < s->loss_p || s->qlen >= s->buf) { sched(s, s->base_rtt + qdelay + 2.0, (int)(f - s->f), 1, 0); continue; }
            s->q[s->qt].flow = (int)(f - s->f); s->q[s->qt].sent_at = s->now; s->qt = (s->qt + 1) % QMAX; s->qlen++;
        }
    }
    /* bottleneck */
    s->credit += s->mu * 0.001;
    while (s->credit >= 1 && s->qlen > 0) {
        qpkt_t *pk = &s->q[s->qh]; s->qh = (s->qh + 1) % QMAX; s->qlen--; s->credit -= 1;
        sched(s, s->base_rtt + s->jitter * rnd(s), pk->flow, 0, pk->sent_at);
    }
    if (s->qlen == 0 && s->credit > 1) s->credit = 1;
}

void sim_run(void *p, double ms) { sim_t *s = p; int n = (int)ms; for (int i = 0; i < n; i++) tick(s); }

static void fill_iv(sim_t *s, flow_t *f) {
    f->iv.dur_ms = s->now - f->iv_start; f->iv.srtt = f->srtt > 0 ? f->srtt : 0; f->iv.min_rtt = f->min_rtt; f->iv.inflight = f->inflight;
}
static void reset_iv(sim_t *s, flow_t *f) { memset(&f->iv, 0, sizeof f->iv); f->iv.min_rtt = f->min_rtt; f->iv_start = s->now; }

/* learned flow: features of the interval that just ended; returns 1 if the interval had any activity */
int sim_features(void *p, int i, double *x, double *aux) {
    sim_t *s = p; flow_t *f = &s->f[i]; fill_iv(s, f);
    int act = f->iv.new_sent > 0 || f->iv.acked > 0 || f->iv.lossev > 0;
    sm_features(&f->hist, &f->iv, f->cwnd, x, aux);
    aux[4] = (double)f->iv.acked; aux[5] = f->iv.dur_ms; aux[6] = f->srtt > 0 && f->min_rtt < 1e8 ? f->srtt / f->min_rtt : 1.0;
    reset_iv(s, f); return act;
}
/* any flow: packets acked in the interval, loss events; resets the interval */
void sim_ivstat(void *p, int i, double *o) { sim_t *s = p; flow_t *f = &s->f[i]; o[0] = (double)f->iv.acked; o[1] = (double)f->iv.lossev; o[2] = s->now - f->iv_start; reset_iv(s, f); }
