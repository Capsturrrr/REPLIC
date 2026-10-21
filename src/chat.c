/*
 * chat.c -- REPLIC Chat: a two-way messenger built on the REPLIC packet format.
 *
 * A demo of what the protocol does for an application. Each message is a
 * DATA packet with a sequence number. The receiver delivers messages strictly
 * in order and answers with cumulative ACKs. The sender keeps a Go-Back-N
 * window, retransmits on a 300 ms timeout and on 3 duplicate ACKs.
 * Packet loss is simulated on the sending side so you can watch messages
 * still arrive complete and in order.
 *
 * Usage:  ./bin/chat <my_port> <peer> <name> [loss_percent]
 *   <peer> is a port (same machine) or ip:port (another machine / the internet).
 * Across the internet: both people run it at about the same time. Each side
 * sends a few small "punch" packets to the other's public address so home
 * routers (NAT) open a return path; the peer address is then learned from
 * the first packet received.
 * Run two copies in two terminals with the ports swapped, e.g.
 *   ./bin/chat 9001 9002 Alice 30
 *   ./bin/chat 9002 9001 Bob   30
 *
 * Uses common.c (13-byte header, checksum). Does not change the tested
 * client/server; this is a separate program.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include "common.h"

#define CHAT_WINDOW 64
#define SRW 256   /* Selective Repeat window, same as the C transport (server.c) */
#define CHAT_TIMEOUT_MS 300
#define MAX_MSGS 4096
#define MSG_MAX 400

static int sockfd;
static struct sockaddr_in peer_addr;
static int loss_percent = 0;
static const char *my_name = "me";

/* sender state */
static char sendq[MAX_MSGS][MSG_MAX + 1];
static uint32_t base = 0;      /* oldest unacked message */
static uint32_t next_seq = 0;  /* next message number to send */
static long long sent_at[MAX_MSGS];
static int dup_acks = 0;
static long recover = -1;   /* highest seq outstanding at the last retransmit; no new fast retransmit until it is acked */

/* receiver state */
static uint32_t expected = 0;

/* Selective Repeat (default; REPLIC_GBN=1 restores Go-Back-N): the receiver buffers a 256-message window of out-of-order
   messages and reports them in a 32-byte bitmap (bit j of byte j/8 = message ack+j received, as in server.c) inside every ACK; the sender keeps one timer per message. */
static int gbn = 0;
static uint8_t sacked[MAX_MSGS];
static char rbuf[SRW][MSG_MAX + 1]; static uint8_t rhave[SRW];

static unsigned long n_sent = 0, n_dropped = 0, n_retx = 0;

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void tx_bytes(uint8_t flags, uint32_t seq, uint32_t ack, const uint8_t *data, int dlen, int is_retx) {
    replic_packet_t p;
    memset(&p, 0, sizeof(p));
    p.seq_num = seq;
    p.ack_num = ack;
    p.flags = flags;
    if (data && dlen > 0) {
        p.payload_len = (uint16_t)dlen;
        memcpy(p.payload, data, p.payload_len);
    }
    uint8_t buf[BUFFER_SIZE + MAX_PAYLOAD];
    int len = replic_pack(&p, buf, sizeof(buf));
    if (len < 0) return;
    n_sent++;
    if (is_retx) n_retx++;
    if (loss_percent > 0 && (rand() % 100) < loss_percent) {
        n_dropped++;
        if (flags & FLAG_DATA)
            printf("      [network lost message #%u%s]\n", seq, is_retx ? " (retransmission)" : "");
        return;
    }
    sendto(sockfd, buf, (size_t)len, 0, (struct sockaddr *)&peer_addr, sizeof(peer_addr));
}

static void tx(uint8_t flags, uint32_t seq, uint32_t ack, const char *text, int is_retx) {
    tx_bytes(flags, seq, ack, (const uint8_t *)text, text ? (int)strlen(text) : 0, is_retx);
}

static void send_window_from(uint32_t from, int is_retx) {
    for (uint32_t s = from; s < next_seq; s++) {
        sent_at[s % MAX_MSGS] = now_ms();
        tx(FLAG_DATA | FLAG_ACK, s, expected, sendq[s % MAX_MSGS], is_retx);
    }
}

/* Selective Repeat: resend only the unreported messages below the highest one the peer reported (or just the base) */
static void resend_holes(void) {
    uint32_t hi = base;
    for (uint32_t s = base; s < next_seq; s++) if (sacked[s % MAX_MSGS]) hi = s;
    for (uint32_t s = base; s <= hi && s < next_seq; s++) if (!sacked[s % MAX_MSGS]) {
        sent_at[s % MAX_MSGS] = now_ms();
        tx(FLAG_DATA | FLAG_ACK, s, expected, sendq[s % MAX_MSGS], 1);
    }
}

static void handle_packet(const uint8_t *buf, ssize_t n) {
    replic_packet_t p;
    int r = replic_unpack(buf, (size_t)n, &p);
    if (r != 0) { printf("      [dropped corrupted packet]\n"); return; }

    /* acknowledgment part (cumulative: ack_num = next message the peer expects) */
    if (p.flags & FLAG_ACK) {
        if (!gbn && !(p.flags & FLAG_DATA) && p.payload_len == SRW / 8) {
            for (int j = 1; j < SRW; j++) if ((p.payload[j / 8] >> (j % 8)) & 1) { uint32_t ix = p.ack_num + (uint32_t)j; if (ix >= base && ix < next_seq) sacked[ix % MAX_MSGS] = 1; }
        }
        if (p.ack_num > base && p.ack_num <= next_seq) {
            for (uint32_t s = base; s < p.ack_num; s++) sacked[s % MAX_MSGS] = 0;
            base = p.ack_num;
            dup_acks = 0;
        } else if (!(p.flags & FLAG_DATA) && p.ack_num == base && base < next_seq) {
            if (++dup_acks >= DUP_ACK_THRESHOLD && (long)base > recover) {
                printf("      [3 duplicate ACKs: fast retransmit from #%u]\n", base);
                if (gbn) send_window_from(base, 1); else resend_holes();
                recover = (long)next_seq - 1; dup_acks = 0;
            }
        }
    }

    /* data part: deliver in order; Selective Repeat buffers out-of-order messages instead of discarding them */
    if (p.flags & FLAG_DATA) {
        if (p.seq_num == expected) {
            char text[MSG_MAX + 1];
            memcpy(text, p.payload, p.payload_len);
            text[p.payload_len] = '\0';
            printf("\r\033[K[#%u] %s\n", p.seq_num, text);
            expected++;
            while (!gbn && rhave[expected % SRW]) {            /* buffered messages that are now in order */
                printf("\r\033[K[#%u] %s\n", expected, rbuf[expected % SRW]);
                rhave[expected % SRW] = 0; expected++;
            }
        } else if (p.seq_num < expected) {
            printf("      [duplicate #%u discarded]\n", p.seq_num);
        } else if (!gbn && p.seq_num < expected + SRW) {
            memcpy(rbuf[p.seq_num % SRW], p.payload, p.payload_len); rbuf[p.seq_num % SRW][p.payload_len] = '\0';
            rhave[p.seq_num % SRW] = 1;
            printf("      [out of order: buffered #%u, expected #%u]\n", p.seq_num, expected);
        } else {
            printf("      [out of order: got #%u, expected #%u, discarded]\n", p.seq_num, expected);
        }
        if (gbn) tx(FLAG_ACK, 0, expected, NULL, 0);
        else {
            uint8_t bm[SRW / 8]; memset(bm, 0, sizeof bm);
            for (uint32_t j = 1; j < SRW; j++) if (rhave[(expected + j) % SRW]) bm[j / 8] |= (uint8_t)(1u << (j % 8));
            tx_bytes(FLAG_ACK, 0, expected, bm, SRW / 8, 0);
        }
    }
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s <my_port> <peer_port> <name> [loss_percent]\n", argv[0]);
        return 1;
    }
    gbn = getenv("REPLIC_GBN") != NULL;
    int my_port = atoi(argv[1]), peer_port;
    char peer_ip[64] = "127.0.0.1";
    const char *colon = strrchr(argv[2], ':');
    if (colon) {
        size_t n = (size_t)(colon - argv[2]);
        if (n >= sizeof(peer_ip)) { fprintf(stderr, "bad peer address\n"); return 1; }
        memcpy(peer_ip, argv[2], n); peer_ip[n] = '\0';
        peer_port = atoi(colon + 1);
    } else peer_port = atoi(argv[2]);
    int remote = strcmp(peer_ip, "127.0.0.1") != 0;
    my_name = argv[3];
    if (argc > 4) loss_percent = atoi(argv[4]);
    srand((unsigned)time(NULL) ^ (unsigned)getpid());
    setvbuf(stdin, NULL, _IONBF, 0);   /* select() must see every pending line: stdio read-ahead made piped or pasted input crawl at 20 lines/s */

    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in me;
    memset(&me, 0, sizeof(me));
    me.sin_family = AF_INET;
    me.sin_addr.s_addr = remote ? htonl(INADDR_ANY) : inet_addr("127.0.0.1");
    me.sin_port = htons((uint16_t)my_port);
    if (bind(sockfd, (struct sockaddr *)&me, sizeof(me)) < 0) { perror("bind"); return 1; }
    memset(&peer_addr, 0, sizeof(peer_addr));
    peer_addr.sin_family = AF_INET;
    peer_addr.sin_addr.s_addr = inet_addr(peer_ip);
    if (peer_addr.sin_addr.s_addr == INADDR_NONE) { fprintf(stderr, "bad peer ip %s\n", peer_ip); return 1; }
    peer_addr.sin_port = htons((uint16_t)peer_port);

    printf("REPLIC Chat as %s on port %d -> peer %s:%d, simulated loss %d%%\n", my_name, my_port, peer_ip, peer_port, loss_percent);
    if (remote) printf("Connecting (hole punching) ... waiting for the other side.\n");
    printf("Type a message and press Enter. Ctrl+D to quit.\n\n");

    int stdin_open = 1, heard = !remote;
    long long last_punch = 0;
    for (;;) {
        if (!heard && now_ms() - last_punch > 1000) {   /* open the NAT path */
            last_punch = now_ms();
            tx(FLAG_ACK, 0, expected, NULL, 0);
        }
        fd_set rf;
        FD_ZERO(&rf);
        FD_SET(sockfd, &rf);
        int maxfd = sockfd;
        int window_full = (next_seq - base) >= CHAT_WINDOW;
        if (stdin_open && !window_full) { FD_SET(0, &rf); }
        struct timeval tv = {0, 50000};
        int rc = select(maxfd + 1, &rf, NULL, NULL, &tv);
        if (rc > 0 && FD_ISSET(sockfd, &rf)) {
            uint8_t buf[BUFFER_SIZE + MAX_PAYLOAD];
            struct sockaddr_in src; socklen_t sl = sizeof(src);
            ssize_t n = recvfrom(sockfd, buf, sizeof(buf), 0, (struct sockaddr *)&src, &sl);
            if (n > 0) {
                replic_packet_t chk;
                if (remote && replic_unpack(buf, (size_t)n, &chk) == 0) {
                    peer_addr = src;   /* follow the peer's real (NAT-mapped) address */
                    if (!heard) { heard = 1; printf("Connected to %s:%d. Type a message.\n\n", inet_ntoa(src.sin_addr), ntohs(src.sin_port)); }
                }
                handle_packet(buf, n);
            }
        }
        if (rc > 0 && stdin_open && FD_ISSET(0, &rf)) {
            char line[MSG_MAX + 2];
            if (!fgets(line, sizeof(line), stdin)) { stdin_open = 0; }
            else {
                line[strcspn(line, "\r\n")] = '\0';
                if (line[0]) {
                    char msg[MSG_MAX + 1];
                    snprintf(msg, sizeof(msg), "%.20s: %.370s", my_name, line);
                    strcpy(sendq[next_seq % MAX_MSGS], msg);
                    sent_at[next_seq % MAX_MSGS] = now_ms();
                    tx(FLAG_DATA | FLAG_ACK, next_seq, expected, msg, 0);
                    next_seq++;
                }
            }
        }
        /* timeout: Go-Back-N resend of everything unacknowledged */
        if (gbn && base < next_seq && now_ms() - sent_at[base % MAX_MSGS] > CHAT_TIMEOUT_MS) {
            printf("      [timeout: resending #%u..#%u]\n", base, next_seq - 1);
            send_window_from(base, 1);
            recover = (long)next_seq - 1;
        }
        if (!gbn) for (uint32_t s = base; s < next_seq; s++)        /* Selective Repeat: one timer per message */
            if (!sacked[s % MAX_MSGS] && now_ms() - sent_at[s % MAX_MSGS] > CHAT_TIMEOUT_MS) {
                printf("      [timeout: resending only #%u]\n", s);
                sent_at[s % MAX_MSGS] = now_ms();
                tx(FLAG_DATA | FLAG_ACK, s, expected, sendq[s % MAX_MSGS], 1);
            }
        if (!stdin_open && base >= next_seq) break;
    }
    printf("\nsent %lu packets, %lu dropped by the simulated network, %lu retransmissions, %u messages delivered to peer\n",
           n_sent, n_dropped, n_retx, base);
    return 0;
}
