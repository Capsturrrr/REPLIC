#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include "common.h"

/*
 * Phase 4 server: same handshake as Phase 3, but now the DATA phase
 * implements a Go-Back-N receiver:
 *
 *   - Tracks `expected_seq`, the next in-order sequence number it wants.
 *   - If an incoming DATA packet's seq == expected_seq: accept it,
 *     deliver the payload, bump expected_seq, send a cumulative ACK
 *     (ack_num = expected_seq).
 *   - If seq != expected_seq (out of order, or a duplicate of something
 *     already delivered): DISCARD the payload and re-send an ACK for
 *     expected_seq. This is what drives the sender's duplicate-ACK
 *     counter and triggers fast retransmit.
 *
 * Go-Back-N deliberately does not buffer out-of-order packets — it's
 * simpler to reason about and implement correctly than Selective Repeat,
 * and it's the classic textbook baseline this project is meant to explore.
 */

typedef enum {
    STATE_LISTEN,
    STATE_SYN_RECEIVED,
    STATE_ESTABLISHED,
} conn_state_t;

static const char *state_name(conn_state_t s) {
    switch (s) {
        case STATE_LISTEN:       return "LISTEN";
        case STATE_SYN_RECEIVED: return "SYN_RECEIVED";
        case STATE_ESTABLISHED:  return "ESTABLISHED";
        default:                 return "UNKNOWN";
    }
}

static void send_packet(int sockfd, const rudp_packet_t *pkt,
                         const struct sockaddr_in *addr, socklen_t addr_len) {
    uint8_t buf[BUFFER_SIZE];
    int len = rudp_pack(pkt, buf, sizeof(buf));
    if (len < 0) {
        fprintf(stderr, "rudp_pack failed\n");
        return;
    }
    if (sendto(sockfd, buf, (size_t)len, 0,
               (const struct sockaddr *)addr, addr_len) < 0) {
        perror("sendto failed");
    }
}

int main(void) {
    int sockfd;
    struct sockaddr_in server_addr, client_addr;
    socklen_t client_len = sizeof(client_addr);
    uint8_t recv_buf[BUFFER_SIZE];
#ifndef RUDP_GBN
    enum { SRW = 256 };   /* receive window: 256 packets, reported as a 32-byte bitmap */
    static uint8_t sack_have[SRW]; static uint8_t sack_buf[SRW][MAX_PAYLOAD]; static uint16_t sack_len[SRW];
#endif

    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        perror("socket creation failed");
        exit(EXIT_FAILURE);
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_addr.s_addr = INADDR_ANY;
    server_addr.sin_port = htons(SERVER_PORT);

    if (bind(sockfd, (const struct sockaddr *)&server_addr, sizeof(server_addr)) < 0) {
        perror("bind failed");
        close(sockfd);
        exit(EXIT_FAILURE);
    }

    printf("RUDP server listening on port %d...\n", SERVER_PORT);

    conn_state_t state = STATE_LISTEN;
    uint32_t server_seq = 0;
    uint32_t expected_seq = 0;   /* Go-Back-N: next in-order seq we want from client */
    char reassembled[65536];
    size_t reassembled_len = 0;
    /* file mode: RUDP_OUT=path writes the delivered byte stream to a file; RUDP_QUIET=1 silences the per-packet log */
    FILE *out_fp = NULL; const char *out_path = getenv("RUDP_OUT"); int quiet = getenv("RUDP_QUIET") != NULL;
    uint64_t fnv = 1469598103934665603ULL; unsigned long long out_bytes = 0;
    (void)quiet;
#define DELIVER(ptr, n) do { if (out_fp) { fwrite((ptr), 1, (n), out_fp); } \
        for (size_t k_ = 0; k_ < (size_t)(n); k_++) { fnv ^= ((const uint8_t *)(ptr))[k_]; fnv *= 1099511628211ULL; } out_bytes += (n); } while (0)

    srand((unsigned int)time(NULL));


    while (1) {
        ssize_t n = recvfrom(sockfd, recv_buf, sizeof(recv_buf), 0,
                              (struct sockaddr *)&client_addr, &client_len);
        if (n < 0) {
            perror("recvfrom failed");
            continue;
        }

        rudp_packet_t pkt;
        int result = rudp_unpack(recv_buf, (size_t)n, &pkt);
        if (result == -2) {
            printf("Dropped corrupted packet (checksum mismatch)\n");
            continue;
        } else if (result != 0) {
            printf("Dropped malformed packet\n");
            continue;
        }

        /* If the client's final handshake ACK was lost, its first DATA packet proves the connection is up (as in TCP). */
        if (state == STATE_SYN_RECEIVED && (pkt.flags & FLAG_DATA) && !(pkt.flags & FLAG_SYN)) {
            state = STATE_ESTABLISHED;
            if (!getenv("RUDP_QUIET")) printf("Handshake ACK was lost; DATA arrived, treating the connection as established\n");
        }

        if (pkt.flags & FLAG_SYN) {
            /* Accept a fresh SYN in ANY state, not just LISTEN.
             * This makes the server resilient to a prior connection being
             * killed mid-session (Ctrl+C, timeout, crash) — without this,
             * a stuck non-LISTEN state would silently reject every future
             * client forever. Real servers must tolerate this. */
            uint32_t client_seq = pkt.seq_num;
            server_seq = (uint32_t)rand();
            expected_seq = client_seq + 1; /* first DATA packet will carry this seq */
            reassembled_len = 0;
            if (out_path && !out_fp) { out_fp = fopen(out_path, "wb"); if (!out_fp) perror("RUDP_OUT"); fnv = 1469598103934665603ULL; out_bytes = 0; }

            rudp_packet_t reply;
            memset(&reply, 0, sizeof(reply));
            reply.seq_num = server_seq;
            reply.ack_num = client_seq + 1;
            reply.flags = FLAG_SYN | FLAG_ACK;
            send_packet(sockfd, &reply, &client_addr, client_len);

            state = STATE_SYN_RECEIVED;
            printf("Handshake: SYN received. State: %s\n", state_name(state));

        } else if ((pkt.flags & FLAG_ACK) && state == STATE_SYN_RECEIVED &&
                   pkt.ack_num == server_seq + 1) {
            state = STATE_ESTABLISHED;
            printf("Handshake complete. State: %s (expecting seq=%u)\n\n",
                   state_name(state), expected_seq);

        } else if ((pkt.flags & FLAG_DATA) && state == STATE_ESTABLISHED) {
#ifndef RUDP_GBN
            /* Selective acknowledgment build: buffer out-of-order packets (64-packet window) and report them
               in an 8-byte bitmap carried in every ACK: bit j set means seq expected_seq+j was received. */
            if (pkt.seq_num == expected_seq) {
                if (reassembled_len + pkt.payload_len < sizeof(reassembled)) {
                    memcpy(reassembled + reassembled_len, pkt.payload, pkt.payload_len);
                    reassembled_len += pkt.payload_len;
                }
                DELIVER(pkt.payload, pkt.payload_len);
                if (!quiet) printf("ACCEPTED seq=%u (%u bytes)\n", pkt.seq_num, pkt.payload_len);
                expected_seq++;
                while (sack_have[expected_seq % SRW]) {          /* buffered packets that are now in order */
                    int slot = (int)(expected_seq % SRW);
                    if (reassembled_len + sack_len[slot] < sizeof(reassembled)) {
                        memcpy(reassembled + reassembled_len, sack_buf[slot], sack_len[slot]);
                        reassembled_len += sack_len[slot];
                    }
                    DELIVER(sack_buf[slot], sack_len[slot]);
                    sack_have[slot] = 0; expected_seq++;
                }
            } else if (pkt.seq_num > expected_seq && pkt.seq_num - expected_seq < SRW) {
                int slot = (int)(pkt.seq_num % SRW);
                if (!sack_have[slot]) {
                    memcpy(sack_buf[slot], pkt.payload, pkt.payload_len); sack_len[slot] = pkt.payload_len;
                    sack_have[slot] = 1;
                }
                if (!quiet) printf("BUFFERED seq=%u (expected %u)\n", pkt.seq_num, expected_seq);
            } else {
                if (!quiet) printf("DUPLICATE seq=%u (expected %u)\n", pkt.seq_num, expected_seq);
            }
            {
                rudp_packet_t ack;
                memset(&ack, 0, sizeof(ack));
                ack.seq_num = server_seq;
                ack.ack_num = expected_seq;
                ack.flags = FLAG_ACK;
                ack.payload_len = SRW / 8;                       /* bit j set = packet expected_seq + j is held */
                for (int j = 1; j < SRW; j++) if (sack_have[(expected_seq + (uint32_t)j) % SRW]) ack.payload[j / 8] |= (uint8_t)(1u << (j % 8));
                send_packet(sockfd, &ack, &client_addr, client_len);
                if (!quiet) printf("  -> ACK %u + sack bitmap\n\n", expected_seq);
            }
#else
            if (pkt.seq_num == expected_seq) {
                /* In-order — accept and deliver */
                if (reassembled_len + pkt.payload_len < sizeof(reassembled)) {
                    memcpy(reassembled + reassembled_len, pkt.payload, pkt.payload_len);
                    reassembled_len += pkt.payload_len;
                }
                printf("ACCEPTED seq=%u (%u bytes): \"%.*s\"\n",
                       pkt.seq_num, pkt.payload_len, pkt.payload_len, pkt.payload);
                DELIVER(pkt.payload, pkt.payload_len);
                expected_seq++;

                rudp_packet_t ack;
                memset(&ack, 0, sizeof(ack));
                ack.seq_num = server_seq;
                ack.ack_num = expected_seq;
                ack.flags = FLAG_ACK;
                send_packet(sockfd, &ack, &client_addr, client_len);
                printf("  -> cumulative ACK %u\n\n", expected_seq);

            } else {
                /* Out of order or duplicate — discard, re-ACK what we actually want */
                printf("DISCARDED seq=%u (expected %u) — out of order/duplicate\n",
                       pkt.seq_num, expected_seq);

                rudp_packet_t ack;
                memset(&ack, 0, sizeof(ack));
                ack.seq_num = server_seq;
                ack.ack_num = expected_seq;
                ack.flags = FLAG_ACK;
                send_packet(sockfd, &ack, &client_addr, client_len);
                printf("  -> duplicate ACK %u\n\n", expected_seq);
            }
#endif

        } else if ((pkt.flags & FLAG_FIN) && state == STATE_ESTABLISHED) {
            rudp_packet_t ack;
            memset(&ack, 0, sizeof(ack));
            ack.seq_num = server_seq;
            ack.ack_num = pkt.seq_num + 1;
            ack.flags = FLAG_ACK;
            send_packet(sockfd, &ack, &client_addr, client_len);

            if (out_path) { if (out_fp) fflush(out_fp); printf("TRANSFER bytes=%llu fnv1a=%016llx\n", out_bytes, (unsigned long long)fnv); fflush(stdout); }
            else printf("Connection closed. Full message reassembled (%zu bytes):\n\"%.*s\"\n\n",
                   reassembled_len, (int)(reassembled_len > 4000 ? 4000 : reassembled_len), reassembled);
            fnv = 1469598103934665603ULL; out_bytes = 0; if (out_fp) { fclose(out_fp); out_fp = NULL; }

            state = STATE_LISTEN;
            printf("State: %s (ready for new connection)\n\n", state_name(state));

        } else {
            printf("Unexpected packet (flags=0x%02X) in state %s — ignored\n\n",
                   pkt.flags, state_name(state));
        }
    }

    close(sockfd);
    return 0;
}
