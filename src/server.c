#define _POSIX_C_SOURCE 200809L
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

static void send_packet(int sockfd, const poise_packet_t *pkt,
                         const struct sockaddr_in *addr, socklen_t addr_len) {
    uint8_t buf[BUFFER_SIZE];
    int len = poise_pack(pkt, buf, sizeof(buf));
    if (len < 0) {
        fprintf(stderr, "poise_pack failed\n");
        return;
    }
    if (sendto(sockfd, buf, (size_t)len, 0,
               (const struct sockaddr *)addr, addr_len) < 0) {
        perror("sendto failed");
    }
}

#define MAXSESS 16
#ifndef POISE_GBN
enum { SRW = 256 };   /* receive window: 256 packets, reported as a 32-byte bitmap */
#endif

/* One session per client address (ip:port), so several clients can talk to the server at the same time. */
typedef struct {
    int used; struct sockaddr_in addr; conn_state_t state; uint32_t server_seq, expected_seq, client_isn; int have_isn; double last_active;
    char reassembled[65536]; size_t reassembled_len;
    FILE *out_fp; uint64_t fnv; unsigned long long out_bytes;
#ifndef POISE_GBN
    uint8_t have[SRW]; uint8_t buf[SRW][MAX_PAYLOAD]; uint16_t len[SRW];
#endif
} sess_t;

static sess_t sessions[MAXSESS];
#define DELIVER(S, ptr, n) do { if ((S)->out_fp) { fwrite((ptr), 1, (n), (S)->out_fp); } \
        for (size_t k_ = 0; k_ < (size_t)(n); k_++) { (S)->fnv ^= ((const uint8_t *)(ptr))[k_]; (S)->fnv *= 1099511628211ULL; } (S)->out_bytes += (n); } while (0)
#define APPEND(S, ptr, n) do { if ((S)->reassembled_len + (n) < sizeof((S)->reassembled)) { memcpy((S)->reassembled + (S)->reassembled_len, (ptr), (n)); (S)->reassembled_len += (n); } } while (0)

static int quiet = 0;
#ifndef POISE_GBN
/* v2 FEC receive state: ring of recently received payloads and the parity packets still waiting for a missing packet */
enum { FR = 512, PG = 16 };
static uint8_t fbuf[MAXSESS][FR][MAX_PAYLOAD]; static uint16_t flen[MAXSESS][FR]; static uint32_t fseq1[MAXSESS][FR];
static uint8_t pbuf[MAXSESS][PG][MAX_PAYLOAD]; static uint32_t pgs[MAXSESS][PG], plx[MAXSESS][PG]; static int pn[MAXSESS][PG], pvalid[MAXSESS][PG];
static long fec_recovered = 0;

static void sr_accept(sess_t *S, uint32_t seq, const uint8_t *pay, uint16_t plen) {
    int si = (int)(S - sessions);
    fseq1[si][seq % FR] = seq + 1; flen[si][seq % FR] = plen; memcpy(fbuf[si][seq % FR], pay, plen);
            if (seq == S->expected_seq) {
                APPEND(S, pay, plen);
                DELIVER(S, pay, plen);
                if (!quiet) printf("ACCEPTED seq=%u (%u bytes)\n", seq, plen);
                S->expected_seq++;
                while (S->have[S->expected_seq % SRW]) {          /* buffered packets that are now in order */
                    int slot = (int)(S->expected_seq % SRW);
                    APPEND(S, S->buf[slot], S->len[slot]);
                    DELIVER(S, S->buf[slot], S->len[slot]);
                    S->have[slot] = 0; S->expected_seq++;
                }
            } else if (seq > S->expected_seq && seq - S->expected_seq < SRW) {
                int slot = (int)(seq % SRW);
                if (!S->have[slot]) { memcpy(S->buf[slot], pay, plen); S->len[slot] = plen; S->have[slot] = 1; }
                if (!quiet) printf("BUFFERED seq=%u (expected %u)\n", seq, S->expected_seq);
            } else {
                if (!quiet) printf("DUPLICATE seq=%u (expected %u)\n", seq, S->expected_seq);
            }
}

static int fec_present(sess_t *S, uint32_t q) { return q < S->expected_seq || S->have[q % SRW]; }
static void fec_try(sess_t *S) {
    int si = (int)(S - sessions), again = 1;
    while (again) {
        again = 0;
        for (int g = 0; g < PG; g++) {
            if (!pvalid[si][g]) continue;
            uint32_t gs = pgs[si][g]; int n = pn[si][g], miss = 0; uint32_t m = 0;
            for (int j = 0; j < n; j++) if (!fec_present(S, gs + (uint32_t)j)) { miss++; m = gs + (uint32_t)j; }
            if (miss == 0 || gs + (uint32_t)n + FR < S->expected_seq) { pvalid[si][g] = 0; continue; }
            if (miss != 1) continue;
            uint8_t rec[MAX_PAYLOAD]; memcpy(rec, pbuf[si][g], MAX_PAYLOAD); uint32_t len = plx[si][g]; int ok = 1;
            for (int j = 0; j < n; j++) {
                uint32_t q = gs + (uint32_t)j; if (q == m) continue;
                if (fseq1[si][q % FR] != q + 1) { ok = 0; break; }
                for (int k = 0; k < flen[si][q % FR]; k++) rec[k] ^= fbuf[si][q % FR][k];
                len ^= flen[si][q % FR];
            }
            if (!ok || len > MAX_PAYLOAD) { pvalid[si][g] = 0; continue; }
            if (m - S->expected_seq >= SRW && m != S->expected_seq) continue;
            sr_accept(S, m, rec, (uint16_t)len); fec_recovered++; pvalid[si][g] = 0; again = 1;
        }
    }
}
#endif


static double mono(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec / 1e9; }

static sess_t *find_sess(const struct sockaddr_in *a, int create) {
    for (int i = 0; i < MAXSESS; i++)
        if (sessions[i].used && sessions[i].addr.sin_port == a->sin_port && sessions[i].addr.sin_addr.s_addr == a->sin_addr.s_addr) return &sessions[i];
    if (!create) return NULL;
    int victim = -1; double oldest = 1e18;
    for (int i = 0; i < MAXSESS; i++) { if (!sessions[i].used) { victim = i; break; } if (sessions[i].last_active < oldest) { oldest = sessions[i].last_active; victim = i; } }
    sess_t *s = &sessions[victim];
    if (s->used && s->out_fp) fclose(s->out_fp);       /* recycled the least recently active session */
    memset(s, 0, sizeof *s); s->used = 1; s->addr = *a; s->state = STATE_LISTEN; s->fnv = 1469598103934665603ULL;
    return s;
}

static int count_active(void) { int n = 0; for (int i = 0; i < MAXSESS; i++) if (sessions[i].used && sessions[i].state != STATE_LISTEN) n++; return n; }

int main(void) {
    int sockfd;
    struct sockaddr_in server_addr, client_addr;
    socklen_t client_len = sizeof(client_addr);
    uint8_t recv_buf[BUFFER_SIZE];

    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) { perror("socket creation failed"); exit(EXIT_FAILURE); }
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET; server_addr.sin_addr.s_addr = INADDR_ANY; int listen_port = getenv("POISE_PORT") ? atoi(getenv("POISE_PORT")) : SERVER_PORT; server_addr.sin_port = htons(listen_port);
    if (bind(sockfd, (const struct sockaddr *)&server_addr, sizeof(server_addr)) < 0) { perror("bind failed"); close(sockfd); exit(EXIT_FAILURE); }
    printf("Poise server listening on port %d (up to %d clients at once)...\n", listen_port, MAXSESS);

    /* file mode: POISE_OUT=path writes the delivered byte stream to a file (path.<clientport> when several clients are active);
       POISE_QUIET=1 silences the per-packet log */
    const char *out_path = getenv("POISE_OUT"); quiet = getenv("POISE_QUIET") != NULL;
    srand((unsigned int)time(NULL));


    while (1) {
        ssize_t n = recvfrom(sockfd, recv_buf, sizeof(recv_buf), 0, (struct sockaddr *)&client_addr, &client_len);
        if (n < 0) { perror("recvfrom failed"); continue; }

        poise_packet_t pkt;
        int result = poise_unpack(recv_buf, (size_t)n, &pkt);
        if (result == -2) { printf("Dropped corrupted packet (checksum mismatch)\n"); continue; }
        else if (result != 0) { printf("Dropped malformed packet\n"); continue; }

        sess_t *S = find_sess(&client_addr, (pkt.flags & FLAG_SYN) != 0);
        if (!S) { if (!quiet) printf("Packet from unknown client ignored\n"); continue; }   /* no session and not a SYN */
        S->last_active = mono();

        /* If the client's final handshake ACK was lost, its first DATA packet proves the connection is up (as in TCP). */
        if (S->state == STATE_SYN_RECEIVED && (pkt.flags & FLAG_DATA) && !(pkt.flags & FLAG_SYN)) {
            S->state = STATE_ESTABLISHED;
            if (!quiet) printf("Handshake ACK was lost; DATA arrived, treating the connection as established\n");
        }

        if (pkt.flags & FLAG_SYN) {
            /* Accept a fresh SYN in ANY state: a client killed mid-session must not block its next attempt. */
            uint32_t client_seq = pkt.seq_num;
            /* v2 fix: a SYN retransmitted because our SYN-ACK was lost carries the same initial sequence number. It is a DUPLICATE of the
               connection we already accepted, not a new one: answer with the SAME SYN-ACK and keep all state (before this the session was
               reset, and its output went to out.bin.<port> while out.bin stayed empty). Netem drops return-path packets; the proxy never did. */
            if (S->state == STATE_SYN_RECEIVED && S->have_isn && S->client_isn == client_seq) {
                poise_packet_t dup; memset(&dup, 0, sizeof(dup));
                dup.seq_num = S->server_seq; dup.ack_num = client_seq + 1; dup.flags = FLAG_SYN | FLAG_ACK;
                send_packet(sockfd, &dup, &client_addr, client_len);
                continue;
            }
            S->client_isn = client_seq; S->have_isn = 1;
            S->server_seq = (uint32_t)rand();
            S->expected_seq = client_seq + 1;
            S->reassembled_len = 0;
#ifndef POISE_GBN
            memset(S->have, 0, sizeof S->have); memset(pvalid[(int)(S - sessions)], 0, sizeof pvalid[0]); memset(fseq1[(int)(S - sessions)], 0, sizeof fseq1[0]);
#endif
            if (S->out_fp) { fclose(S->out_fp); S->out_fp = NULL; }
            if (out_path) {
                char path[512];
                if (count_active() - (S->state != STATE_LISTEN ? 1 : 0) > 0) snprintf(path, sizeof path, "%s.%u", out_path, ntohs(client_addr.sin_port)); else snprintf(path, sizeof path, "%s", out_path);
                S->out_fp = fopen(path, "wb"); if (!S->out_fp) perror("POISE_OUT");
            }
            S->fnv = 1469598103934665603ULL; S->out_bytes = 0;

            poise_packet_t reply;
            memset(&reply, 0, sizeof(reply));
            reply.seq_num = S->server_seq; reply.ack_num = client_seq + 1; reply.flags = FLAG_SYN | FLAG_ACK;
            send_packet(sockfd, &reply, &client_addr, client_len);
            S->state = STATE_SYN_RECEIVED;
            printf("Handshake: SYN received from port %u. State: %s\n", ntohs(client_addr.sin_port), state_name(S->state));

        } else if ((pkt.flags & FLAG_ACK) && S->state == STATE_SYN_RECEIVED && pkt.ack_num == S->server_seq + 1) {
            S->state = STATE_ESTABLISHED;
            printf("Handshake complete. State: %s (expecting seq=%u)\n\n", state_name(S->state), S->expected_seq);

        } else if ((pkt.flags & FLAG_FEC) && S->state == STATE_ESTABLISHED) {
#ifndef POISE_GBN
            int si = (int)(S - sessions), g = (int)((pkt.seq_num / 8) % PG);
            for (int q = 0; q < PG; q++) if (!pvalid[si][q]) { g = q; break; }
            memset(pbuf[si][g], 0, MAX_PAYLOAD); memcpy(pbuf[si][g], pkt.payload, pkt.payload_len);
            pgs[si][g] = pkt.seq_num; pn[si][g] = (int)(pkt.ack_num >> 16); plx[si][g] = pkt.ack_num & 0xFFFF; pvalid[si][g] = 1;
            uint32_t before = S->expected_seq; fec_try(S);
            if (S->expected_seq != before || 1) {
                poise_packet_t ack; memset(&ack, 0, sizeof ack);
                ack.seq_num = S->server_seq; ack.ack_num = S->expected_seq; ack.flags = FLAG_ACK; ack.payload_len = SRW / 8;
                for (int j = 1; j < SRW; j++) if (S->have[(S->expected_seq + (uint32_t)j) % SRW]) ack.payload[j / 8] |= (uint8_t)(1u << (j % 8));
                send_packet(sockfd, &ack, &client_addr, client_len);
            }
#endif
        } else if ((pkt.flags & FLAG_DATA) && S->state == STATE_ESTABLISHED) {
#ifndef POISE_GBN
            /* Selective acknowledgment build: buffer out-of-order packets (256-packet window) and report them
               in a 32-byte bitmap carried in every ACK: bit j set means seq expected_seq+j was received. */
            sr_accept(S, pkt.seq_num, pkt.payload, pkt.payload_len);
            fec_try(S);
            {
                poise_packet_t ack;
                memset(&ack, 0, sizeof(ack));
                ack.seq_num = S->server_seq; ack.ack_num = S->expected_seq; ack.flags = FLAG_ACK;
                ack.payload_len = SRW / 8;                       /* bit j set = packet expected_seq + j is held */
                for (int j = 1; j < SRW; j++) if (S->have[(S->expected_seq + (uint32_t)j) % SRW]) ack.payload[j / 8] |= (uint8_t)(1u << (j % 8));
                send_packet(sockfd, &ack, &client_addr, client_len);
                if (!quiet) printf("  -> ACK %u + sack bitmap\n\n", S->expected_seq);
            }
#else
            poise_packet_t ack;
            memset(&ack, 0, sizeof(ack));
            ack.seq_num = S->server_seq; ack.flags = FLAG_ACK;
            if (pkt.seq_num == S->expected_seq) {
                APPEND(S, pkt.payload, pkt.payload_len);
                if (!quiet) printf("ACCEPTED seq=%u (%u bytes): \"%.*s\"\n", pkt.seq_num, pkt.payload_len, pkt.payload_len, pkt.payload);
                DELIVER(S, pkt.payload, pkt.payload_len);
                S->expected_seq++;
                ack.ack_num = S->expected_seq;
                send_packet(sockfd, &ack, &client_addr, client_len);
                if (!quiet) printf("  -> cumulative ACK %u\n\n", S->expected_seq);
            } else {
                if (!quiet) printf("DISCARDED seq=%u (expected %u) - out of order/duplicate\n", pkt.seq_num, S->expected_seq);
                ack.ack_num = S->expected_seq;
                send_packet(sockfd, &ack, &client_addr, client_len);
                if (!quiet) printf("  -> duplicate ACK %u\n\n", S->expected_seq);
            }
#endif

        } else if ((pkt.flags & FLAG_FIN) && S->state == STATE_ESTABLISHED) {
            poise_packet_t ack;
            memset(&ack, 0, sizeof(ack));
            ack.seq_num = S->server_seq; ack.ack_num = pkt.seq_num + 1; ack.flags = FLAG_ACK;
            send_packet(sockfd, &ack, &client_addr, client_len);

            if (out_path) { if (S->out_fp) fflush(S->out_fp); printf("TRANSFER bytes=%llu fnv1a=%016llx\n", S->out_bytes, (unsigned long long)S->fnv); fflush(stdout); }
            else printf("Connection closed. Full message reassembled (%zu bytes):\n\"%.*s\"\n\n",
                   S->reassembled_len, (int)(S->reassembled_len > 4000 ? 4000 : S->reassembled_len), S->reassembled);
            if (S->out_fp) { fclose(S->out_fp); S->out_fp = NULL; }
            S->fnv = 1469598103934665603ULL; S->out_bytes = 0;
            S->state = STATE_LISTEN;
            printf("State: %s (ready for new connection)\n\n", state_name(S->state));

        } else if ((pkt.flags & FLAG_FIN) && S->state == STATE_LISTEN) {
            /* a retransmitted FIN after we already closed: acknowledge again so the client can finish */
            poise_packet_t ack;
            memset(&ack, 0, sizeof(ack));
            ack.seq_num = S->server_seq; ack.ack_num = pkt.seq_num + 1; ack.flags = FLAG_ACK;
            send_packet(sockfd, &ack, &client_addr, client_len);
        } else {
            printf("Unexpected packet (flags=0x%02X) in state %s - ignored\n\n", pkt.flags, state_name(S->state));
        }
    }
    close(sockfd);
    return 0;
}
