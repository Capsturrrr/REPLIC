#ifndef DEEP_CC_H
#define DEEP_CC_H
/*
 * REPLIC deep agent, inference only: a small neural network (5 -> 32 -> 32 -> 5, ReLU)
 * that scores the five window actions. Weights are trained in Python (web/train_deep.py)
 * and exported to a text file by web/export_dqn.py.
 * Input features: RTT growth, loss fraction, window size, throughput share, last action.
 */
#include <stdio.h>
#define DN_IN 5
#define DN_H 32
#define DN_OUT 5
typedef struct {
    double W1[DN_IN][DN_H], b1[DN_H], W2[DN_H][DN_H], b2[DN_H], W3[DN_H][DN_OUT], b3[DN_OUT];
} deep_net_t;

static inline int deep_load(deep_net_t *n, const char *path) {
    FILE *f = fopen(path, "r"); if (!f) return -1;
    int ok = 1;
    for (int i = 0; i < DN_IN && ok; i++) for (int j = 0; j < DN_H && ok; j++) ok = fscanf(f, "%lf", &n->W1[i][j]) == 1;
    for (int j = 0; j < DN_H && ok; j++) ok = fscanf(f, "%lf", &n->b1[j]) == 1;
    for (int i = 0; i < DN_H && ok; i++) for (int j = 0; j < DN_H && ok; j++) ok = fscanf(f, "%lf", &n->W2[i][j]) == 1;
    for (int j = 0; j < DN_H && ok; j++) ok = fscanf(f, "%lf", &n->b2[j]) == 1;
    for (int i = 0; i < DN_H && ok; i++) for (int j = 0; j < DN_OUT && ok; j++) ok = fscanf(f, "%lf", &n->W3[i][j]) == 1;
    for (int j = 0; j < DN_OUT && ok; j++) ok = fscanf(f, "%lf", &n->b3[j]) == 1;
    fclose(f); return ok ? 0 : -1;
}
static inline void deep_forward(const deep_net_t *n, const double *x, double *q) {
    double h1[DN_H], h2[DN_H];
    for (int j = 0; j < DN_H; j++) { double s = n->b1[j]; for (int i = 0; i < DN_IN; i++) s += x[i] * n->W1[i][j]; h1[j] = s > 0 ? s : 0; }
    for (int j = 0; j < DN_H; j++) { double s = n->b2[j]; for (int i = 0; i < DN_H; i++) s += h1[i] * n->W2[i][j]; h2[j] = s > 0 ? s : 0; }
    for (int j = 0; j < DN_OUT; j++) { double s = n->b3[j]; for (int i = 0; i < DN_H; i++) s += h2[i] * n->W3[i][j]; q[j] = s; }
}
#endif
