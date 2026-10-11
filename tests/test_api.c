/* librudp API test: two consecutive transfers from one process (a buffer and a file), over a path with 5% emulated loss.
 * usage: test_api PORT  (a server with RUDP_OUT=<path> must be running); prints the payload hashes for the script to compare. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rudp.h"

int main(int argc, char **argv) {
    int port = argc > 1 ? atoi(argv[1]) : 8888; int fail = 0;
    rudp_options_t o; rudp_options_default(&o); o.port = port; o.emulate = 1; o.loss_pct = 5; o.delay_ms = 5; o.rate_pps = 4000; o.seed = 3;
    rudp_stats_t st;
    /* argument checks */
    if (rudp_send(&o, NULL, 10, &st) != RUDP_ERR_ARGS) { puts("FAIL: NULL data accepted"); fail = 1; }
    if (rudp_send(&o, "x", 0, &st) != RUDP_ERR_ARGS) { puts("FAIL: empty data accepted"); fail = 1; }
    o.host = "not-an-ip"; if (rudp_send(&o, "x", 1, &st) != RUDP_ERR_ARGS) { puts("FAIL: bad host accepted"); fail = 1; }
    o.host = "127.0.0.1";
    /* 1: a buffer, 2: the same payload again with another policy (state must not leak between calls) */
    size_t n = 70000; unsigned char *buf = malloc(n); for (size_t i = 0; i < n; i++) buf[i] = (unsigned char)((i * 7 + i / 251) & 255);
    const char *modes[2] = { "aimd", "cubic" };
    for (int k = 0; k < 2; k++) {
        o.mode = modes[k];
        int rc = rudp_send(&o, buf, n, &st);
        if (rc != RUDP_OK || st.bytes != (long)n) { printf("FAIL: transfer %d rc=%d bytes=%ld\n", k, rc, st.bytes); fail = 1; }
        else printf("HASH %d %016llx %ld\n", k, (unsigned long long)st.fnv1a, st.retransmissions);
    }
    free(buf);
    return fail;
}
