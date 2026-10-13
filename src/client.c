#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <sys/time.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include "common.h"

#define HANDSHAKE_TIMEOUT_SEC 3
#define MAX_HANDSHAKE_RETRIES 5
#define MAX_CHUNKS 128

/*
 * Phase 5 client: same Go-Back-N reliability mechanics as Phase 4
 * (retransmission timeout, fast retransmit on 3 duplicate ACKs), but
 * now the window size is no longer a fixed constant — it's driven by
 * a congestion window `cwnd` that grows and shrinks using AIMD:
 *
 *   SLOW START:
 *     cwnd < ssthresh -> cwnd grows by ~1 per ACK (roughly doubles per RTT)
 *
 *   CONGESTION AVOIDANCE:
 *     cwnd >= ssthresh -> cwnd grows by ~1/cwnd per ACK (roughly +1 per RTT,
 *     i.e. linear growth instead of exponential)
 *
 *   ON TIMEOUT (loss detected the slow way):
 *     ssthresh = cwnd / 2;  cwnd = 1        -> back to slow start
 *
 *   ON FAST RETRANSMIT (loss detected fast, via 3 dup ACKs):
 *     ssthresh = cwnd / 2;  cwnd = ssthresh -> smaller penalty, resume
 *     directly in congestion avoidance (this is "fast recovery")
 *
 * Every cwnd change is logged to logs/cwnd_log.csv for Phase 7 graphing.
 */

static int loss_percent = 0;
static FILE *cwnd_log = NULL;
static long log_start_ms = 0;

static int should_drop(void) {
    if (loss_percent <= 0) return 0;
    return (rand() % 100) < loss_percent;
}

static long now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static void log_cwnd(const char *event, double cwnd, double ssthresh) {
    if (!cwnd_log) return;
    fprintf(cwnd_log, "%ld,%s,%.3f,%.3f\n", now_ms() - log_start_ms, event, cwnd, ssthresh);
    fflush(cwnd_log);
}

static void send_packet(int sockfd, const poise_packet_t *pkt,
                         const struct sockaddr_in *addr, socklen_t addr_len,
                         int allow_drop) {
    uint8_t buf[BUFFER_SIZE];
    int len = poise_pack(pkt, buf, sizeof(buf));
    if (len < 0) {
        fprintf(stderr, "poise_pack failed\n");
        return;
    }
    if (allow_drop && should_drop()) {
        printf("  [SIMULATED LOSS] dropped seq=%u\n", pkt->seq_num);
        return;
    }
    if (sendto(sockfd, buf, (size_t)len, 0,
               (const struct sockaddr *)addr, addr_len) < 0) {
        perror("sendto failed");
    }
}

int main(int argc, char *argv[]) {
    if (argc > 1) {
        loss_percent = atoi(argv[1]);
        printf("Simulated packet loss: %d%%\n", loss_percent);
    }

    system("mkdir -p logs");
    cwnd_log = fopen("logs/cwnd_log.csv", "w");
    if (cwnd_log) {
        fprintf(cwnd_log, "time_ms,event,cwnd,ssthresh\n");
    }
    log_start_ms = now_ms();

    int sockfd;
    struct sockaddr_in server_addr;
    socklen_t server_len = sizeof(server_addr);
    uint8_t recv_buf[BUFFER_SIZE];

    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        perror("socket creation failed");
        exit(EXIT_FAILURE);
    }

    struct timeval tv;
    tv.tv_sec = HANDSHAKE_TIMEOUT_SEC;
    tv.tv_usec = 0;
    setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(SERVER_PORT);
    inet_pton(AF_INET, SERVER_IP, &server_addr.sin_addr);

    srand((unsigned int)time(NULL));
    uint32_t client_seq = (uint32_t)rand();
    uint32_t server_seq = 0;

    /* --- Handshake (unchanged) --- */
    poise_packet_t syn;
    memset(&syn, 0, sizeof(syn));
    syn.seq_num = client_seq;
    syn.flags = FLAG_SYN;

    int handshake_ok = 0;
    for (int attempt = 1; attempt <= MAX_HANDSHAKE_RETRIES; attempt++) {
        printf("Sending SYN (attempt %d)...\n", attempt);
        send_packet(sockfd, &syn, &server_addr, server_len, 0);

        poise_packet_t reply;
        ssize_t n = recvfrom(sockfd, recv_buf, sizeof(recv_buf), 0, NULL, NULL);
        if (n < 0) { printf("Timed out, retrying...\n"); continue; }
        if (poise_unpack(recv_buf, (size_t)n, &reply) != 0) continue;

        if ((reply.flags & FLAG_SYN) && (reply.flags & FLAG_ACK) &&
            reply.ack_num == client_seq + 1) {
            server_seq = reply.seq_num;

            poise_packet_t ack;
            memset(&ack, 0, sizeof(ack));
            ack.seq_num = client_seq + 1;
            ack.ack_num = server_seq + 1;
            ack.flags = FLAG_ACK;
            send_packet(sockfd, &ack, &server_addr, server_len, 0);

            handshake_ok = 1;
            break;
        }
    }

    if (!handshake_ok) {
        fprintf(stderr, "Handshake failed.\n");
        close(sockfd);
        exit(EXIT_FAILURE);
    }

    printf("Handshake complete. Connection established.\n\n");

    /* --- Build a longer message so cwnd has room to actually grow --- */
    char message[4096];
    message[0] = '\0';
    const char *phrase =
        "Poise congestion control test data block. AIMD paces cwnd growth. ";
    while (strlen(message) + strlen(phrase) < sizeof(message) - 1 &&
           strlen(message) < 2400) {
        strcat(message, phrase);
    }

    size_t msg_len = strlen(message);
    size_t chunk_size = 40;
    int total_packets = (int)((msg_len + chunk_size - 1) / chunk_size);
    if (total_packets > MAX_CHUNKS) total_packets = MAX_CHUNKS;

    uint32_t base_seq = client_seq + 1;
    poise_packet_t packets[MAX_CHUNKS];

    for (int i = 0; i < total_packets; i++) {
        memset(&packets[i], 0, sizeof(poise_packet_t));
        size_t offset = (size_t)i * chunk_size;
        size_t len = (offset + chunk_size <= msg_len) ? chunk_size : (msg_len - offset);
        packets[i].seq_num = base_seq + (uint32_t)i;
        packets[i].flags = FLAG_DATA;
        packets[i].payload_len = (uint16_t)len;
        memcpy(packets[i].payload, message + offset, len);
    }

    printf("Sending %d packets (%zu bytes total) with AIMD congestion control...\n\n",
           total_packets, msg_len);

    /* --- Congestion-controlled sliding window send loop --- */
    int base = 0;
    int next_seq = 0;
    long timer_start = 0;
    int timer_active = 0;
    uint32_t last_ack_seen = 0;
    int dup_ack_count = 0;
    /* Selective Repeat (default): the server buffers out-of-order packets and returns a bitmap of them in every ACK;
       the client keeps one timer per packet and resends only what is missing. Set POISE_GBN=1 (and build the server
       with -DPOISE_GBN) for the original Go-Back-N behaviour. */
    int selective = getenv("POISE_GBN") == NULL;
    long sent_ms[MAX_CHUNKS]; uint8_t sacked[MAX_CHUNKS]; memset(sacked, 0, sizeof sacked); memset(sent_ms, 0, sizeof sent_ms);

    double cwnd = INITIAL_CWND;
    double ssthresh = INITIAL_SSTHRESH;
    log_cwnd("START", cwnd, ssthresh);

    tv.tv_sec = 0;
    tv.tv_usec = 50000;
    setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    while (base < total_packets) {
        int window_limit = base + (int)cwnd;

        while (next_seq < total_packets && next_seq < window_limit) {
            send_packet(sockfd, &packets[next_seq], &server_addr, server_len, 1);
            sent_ms[next_seq] = now_ms();
            if (!timer_active) { timer_start = now_ms(); timer_active = 1; }
            next_seq++;
        }

        poise_packet_t ack;
        ssize_t n = recvfrom(sockfd, recv_buf, sizeof(recv_buf), 0, NULL, NULL);

        if (n > 0 && poise_unpack(recv_buf, (size_t)n, &ack) == 0 && (ack.flags & FLAG_ACK)) {
            uint32_t acked_seq = ack.ack_num;
            uint32_t base_pkt_seq = packets[base].seq_num;
            if (selective && ack.payload_len == 32) {
                for (int j = 1; j < 256; j++) if (ack.payload[j / 8] >> (j % 8) & 1) { long ix = (long)(acked_seq + (uint32_t)j) - (long)base_seq; if (ix >= 0 && ix < total_packets) sacked[ix] = 1; }
            }
            if (acked_seq > base_pkt_seq) {
                while (base < total_packets && packets[base].seq_num < acked_seq) base++;

                /* AIMD growth on every new ACK */
                if (cwnd < ssthresh) {
                    cwnd += 1.0; /* slow start: exponential-ish growth */
                } else {
                    cwnd += 1.0 / cwnd; /* congestion avoidance: linear growth */
                }
                if (cwnd > MAX_CWND) cwnd = MAX_CWND;

                printf("  <- ACK %u — base=%d  cwnd=%.2f ssthresh=%.2f (%s)\n",
                       acked_seq, base, cwnd, ssthresh,
                       cwnd < ssthresh ? "slow start" : "congestion avoidance");
                log_cwnd("ACK", cwnd, ssthresh);

                dup_ack_count = 0;
                last_ack_seen = acked_seq;
                timer_active = (base < total_packets);
                if (timer_active) timer_start = now_ms();

            } else if (acked_seq == last_ack_seen && acked_seq == base_pkt_seq) {
                dup_ack_count++;

                if (dup_ack_count >= DUP_ACK_THRESHOLD) {
                    /* Fast retransmit + fast recovery: halve, don't collapse to 1 */
                    ssthresh = cwnd / 2.0;
                    if (ssthresh < MIN_CWND) ssthresh = MIN_CWND;
                    cwnd = ssthresh;

                    printf("  !! FAST RETRANSMIT seq=%u — cwnd HALVED to %.2f (ssthresh=%.2f)\n",
                           packets[base].seq_num, cwnd, ssthresh);
                    log_cwnd("FAST_RETRANSMIT", cwnd, ssthresh);

                    if (selective) { /* resend the holes below the highest packet the receiver reported */
                        int hi = base + 1; for (int i = base; i < next_seq; i++) if (sacked[i]) hi = i;
                        for (int i = base; i < hi; i++) if (!sacked[i]) { send_packet(sockfd, &packets[i], &server_addr, server_len, 1); sent_ms[i] = now_ms(); }
                    } else send_packet(sockfd, &packets[base], &server_addr, server_len, 1);
                    dup_ack_count = 0;
                    timer_start = now_ms();
                }
            } else {
                last_ack_seen = acked_seq;
            }

        } else {
            int expired = timer_active && (now_ms() - timer_start) >= TIMEOUT_MS;
            if (selective) { expired = 0; for (int i = base; i < next_seq; i++) if (!sacked[i] && now_ms() - sent_ms[i] >= TIMEOUT_MS) { expired = 1; break; } }
            if (expired) {
                /* Timeout: harsher penalty — collapse back to slow start */
                ssthresh = cwnd / 2.0;
                if (ssthresh < MIN_CWND) ssthresh = MIN_CWND;
                cwnd = INITIAL_CWND;

                printf("  !! TIMEOUT — cwnd RESET to %.2f (ssthresh=%.2f), resending unreported packets from base=%d\n",
                       cwnd, ssthresh, base);
                log_cwnd("TIMEOUT", cwnd, ssthresh);

                for (int i = base; i < next_seq; i++) {
                    if (selective && (sacked[i] || now_ms() - sent_ms[i] < TIMEOUT_MS)) continue;   /* Selective Repeat: only expired, unreported packets */
                    send_packet(sockfd, &packets[i], &server_addr, server_len, 1);
                    sent_ms[i] = now_ms();
                }
                timer_start = now_ms();
                dup_ack_count = 0;
            }
        }
    }

    printf("\nAll %d packets delivered. Final cwnd=%.2f ssthresh=%.2f\n\n",
           total_packets, cwnd, ssthresh);
    log_cwnd("DONE", cwnd, ssthresh);

    /* --- Teardown --- */
    poise_packet_t fin;
    memset(&fin, 0, sizeof(fin));
    fin.seq_num = base_seq + (uint32_t)total_packets;
    fin.flags = FLAG_FIN;
    send_packet(sockfd, &fin, &server_addr, server_len, 0);

    tv.tv_sec = 2;
    tv.tv_usec = 0;
    setsockopt(sockfd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    recvfrom(sockfd, recv_buf, sizeof(recv_buf), 0, NULL, NULL);

    printf("Connection closed.\n");
    if (cwnd_log) fclose(cwnd_log);
    close(sockfd);
    return 0;
}
