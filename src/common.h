#ifndef COMMON_H
#define COMMON_H

#include <stdint.h>
#include <stddef.h>

#ifndef SERVER_PORT
#define SERVER_PORT 8888
#endif
#define SERVER_IP "127.0.0.1"
#define BUFFER_SIZE 2048
#define MAX_PAYLOAD 1200

/* Flag bits — a packet can carry more than one, e.g. SYN+ACK */
#define FLAG_SYN  0x01
#define FLAG_ACK  0x02
#define FLAG_FIN  0x04
#define FLAG_DATA 0x08
#define FLAG_FEC  0x10   /* v2: XOR parity packet over a group of data packets */

/* Phase 4: reliable delivery tuning */
#define WINDOW_SIZE 4          /* legacy fixed window, unused once congestion control drives cwnd */
#define TIMEOUT_MS 500         /* retransmission timeout per packet */
#define DUP_ACK_THRESHOLD 3    /* fast retransmit trigger */

/* Phase 5: congestion control tuning */
#define INITIAL_CWND 1.0       /* start conservative, like real TCP */
#define INITIAL_SSTHRESH 16.0  /* slow start ceiling before switching to linear growth */
#define MIN_CWND 1.0           /* never shrink below one packet in flight */
#define MAX_CWND 32.0          /* cap so a lossless run doesn't grow unbounded */

/*
 * Poise packet header, sent on the wire in this exact byte layout:
 *   seq_num      4 bytes
 *   ack_num      4 bytes
 *   flags        1 byte
 *   checksum     2 bytes
 *   payload_len  2 bytes
 *   payload      payload_len bytes
 *
 * We do NOT send this struct directly with sizeof() — struct padding
 * differs across compilers/platforms. Instead we manually serialize
 * each field into a flat byte buffer (see poise_pack / poise_unpack).
 */
typedef struct {
    uint32_t seq_num;
    uint32_t ack_num;
    uint8_t  flags;
    uint16_t checksum;
    uint16_t payload_len;
    uint8_t  payload[MAX_PAYLOAD];
} poise_packet_t;

/* Fixed size of the header on the wire (excludes payload) */
#define POISE_HEADER_SIZE 13  /* 4 + 4 + 1 + 2 + 2 */

int poise_pack(const poise_packet_t *pkt, uint8_t *buf, size_t buf_size);
int poise_unpack(const uint8_t *buf, size_t len, poise_packet_t *pkt);
uint16_t poise_checksum(const poise_packet_t *pkt);
void poise_print_packet(const char *label, const poise_packet_t *pkt);

#endif /* COMMON_H */
