/* Minimal librudp example: send a file's bytes (or a string) to a RUDP server.
 *   make examples && RUDP_OUT=/tmp/out.bin ./bin/server &        # receiver
 *   ./bin/example_send 8888 some_file                              # sender (neural agent if weights are present)
 */
#include <stdio.h>
#include <stdlib.h>
#include "rudp.h"

int main(int argc, char **argv) {
    int port = argc > 1 ? atoi(argv[1]) : 8888;
    rudp_options_t o; rudp_options_default(&o);
    o.port = port;
    o.mode = "deep"; o.weights_file = "results/dqn_chat.txt";      /* the neural agent; remove these two lines for plain AIMD */
    rudp_stats_t st; int rc;
    if (argc > 2) rc = rudp_send_file(&o, argv[2], &st);
    else { const char *msg = "hello over RUDP"; rc = rudp_send(&o, msg, 15, &st); }
    if (rc != RUDP_OK) { fprintf(stderr, "send failed: %s\n", rudp_strerror(rc)); return 1; }
    printf("sent %ld bytes in %.0f ms (%ld transmissions, %ld retransmissions), fnv1a %016llx\n",
           st.bytes, st.elapsed_ms, st.transmissions, st.retransmissions, (unsigned long long)st.fnv1a);
    return 0;
}
