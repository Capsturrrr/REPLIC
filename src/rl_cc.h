#ifndef RL_CC_H
#define RL_CC_H
/*
 * Poise: tabular Q-learning congestion-window controller.
 *
 * State  (80) = RTT-ratio bucket (4) x loss bucket (4) x cwnd bucket (5)
 * Action (5)  = multiply cwnd by {0.5, 0.85, 1.0, 1.15, 1.5}
 * Reward      = thr/ref - 0.5*max(0, rtt_ratio-1) - 4*loss_fraction
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define RL_NS 80
#define RL_NA 5
static const double RL_MULT[RL_NA] = {0.5, 0.85, 1.0, 1.15, 1.5};

typedef struct {
    double q[RL_NS][RL_NA];
    double alpha, gamma, eps;
    int    trained_updates;
} rl_agent_t;

static inline int rl_rtt_bucket(double r)  { return r < 1.15 ? 0 : r < 1.5 ? 1 : r < 2.2 ? 2 : 3; }
static inline int rl_loss_bucket(double l) { return l <= 0.0 ? 0 : l < 0.03 ? 1 : l < 0.10 ? 2 : 3; }
static inline int rl_cwnd_bucket(double c) { return c < 4 ? 0 : c < 12 ? 1 : c < 32 ? 2 : c < 64 ? 3 : 4; }
static inline int rl_state(double rtt_ratio, double loss, double cwnd) {
    return (rl_rtt_bucket(rtt_ratio) * 4 + rl_loss_bucket(loss)) * 5 + rl_cwnd_bucket(cwnd);
}

/* Prior: gently prefer growing when the path looks clean, shrinking when congested. */
static inline void rl_init(rl_agent_t *a, double alpha, double gamma, double eps) {
    for (int s = 0; s < RL_NS; s++) {
        int cb_loss = (s / 5) % 4, cb_rtt = (s / 5) / 4;
        double d = (cb_rtt <= 1 && cb_loss <= 1) ? 1.0 : (cb_rtt >= 2 || cb_loss >= 2) ? -1.0 : 0.0;
        for (int k = 0; k < RL_NA; k++)
            a->q[s][k] = 0.25 * d * log(RL_MULT[k]) / log(1.5);
    }
    a->alpha = alpha; a->gamma = gamma; a->eps = eps; a->trained_updates = 0;
}

static inline int rl_best(const rl_agent_t *a, int s) {
    int b = 0;
    for (int k = 1; k < RL_NA; k++) if (a->q[s][k] > a->q[s][b]) b = k;
    return b;
}
static inline int rl_choose(const rl_agent_t *a, int s) {
    if (a->eps > 0 && (rand() / (double)RAND_MAX) < a->eps) return rand() % RL_NA;
    return rl_best(a, s);
}
static inline void rl_update(rl_agent_t *a, int s, int act, double r, int s2) {
    double target = r + a->gamma * a->q[s2][rl_best(a, s2)];
    a->q[s][act] += a->alpha * (target - a->q[s][act]);
    a->trained_updates++;
}
static inline int rl_save(const rl_agent_t *a, const char *path) {
    FILE *f = fopen(path, "w"); if (!f) return -1;
    fprintf(f, "%d\n", a->trained_updates);
    for (int s = 0; s < RL_NS; s++) {
        for (int k = 0; k < RL_NA; k++) fprintf(f, "%.6f ", a->q[s][k]);
        fprintf(f, "\n");
    }
    fclose(f); return 0;
}
static inline int rl_load(rl_agent_t *a, const char *path) {
    FILE *f = fopen(path, "r"); if (!f) return -1;
    if (fscanf(f, "%d", &a->trained_updates) != 1) { fclose(f); return -1; }
    for (int s = 0; s < RL_NS; s++)
        for (int k = 0; k < RL_NA; k++)
            if (fscanf(f, "%lf", &a->q[s][k]) != 1) { fclose(f); return -1; }
    fclose(f); return 0;
}
#endif
