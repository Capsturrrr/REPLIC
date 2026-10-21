#include <string.h>
#include <stdio.h>
#include "common.h"

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over seq, ack, flags, payload_len and payload, in wire order.
   The first versions used a plain byte sum, which cannot detect swapped or compensating bytes (see tests/fuzz_unpack.c);
   the field is still 16 bits, so the header layout and all tools that parse it are unchanged. */
static uint16_t crc16_byte(uint16_t crc, uint8_t b) {
    crc ^= (uint16_t)b << 8;
    for (int k = 0; k < 8; k++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    return crc;
}

uint16_t replic_checksum(const replic_packet_t *pkt) {
    uint16_t crc = 0xFFFF;
    for (int sh = 24; sh >= 0; sh -= 8) crc = crc16_byte(crc, (uint8_t)(pkt->seq_num >> sh));
    for (int sh = 24; sh >= 0; sh -= 8) crc = crc16_byte(crc, (uint8_t)(pkt->ack_num >> sh));
    crc = crc16_byte(crc, pkt->flags);
    crc = crc16_byte(crc, (uint8_t)(pkt->payload_len >> 8));
    crc = crc16_byte(crc, (uint8_t)pkt->payload_len);
    for (uint16_t i = 0; i < pkt->payload_len; i++) crc = crc16_byte(crc, pkt->payload[i]);
    return crc;
}

int replic_pack(const replic_packet_t *pkt, uint8_t *buf, size_t buf_size) {
    if (buf == NULL || pkt == NULL) return -1;

    size_t total = REPLIC_HEADER_SIZE + pkt->payload_len;
    if (buf_size < total) return -1;
    if (pkt->payload_len > MAX_PAYLOAD) return -1;

    uint16_t chk = replic_checksum(pkt);

    size_t off = 0;

    buf[off++] = (pkt->seq_num >> 24) & 0xFF;
    buf[off++] = (pkt->seq_num >> 16) & 0xFF;
    buf[off++] = (pkt->seq_num >> 8)  & 0xFF;
    buf[off++] = (pkt->seq_num)       & 0xFF;

    buf[off++] = (pkt->ack_num >> 24) & 0xFF;
    buf[off++] = (pkt->ack_num >> 16) & 0xFF;
    buf[off++] = (pkt->ack_num >> 8)  & 0xFF;
    buf[off++] = (pkt->ack_num)       & 0xFF;

    buf[off++] = pkt->flags;

    buf[off++] = (chk >> 8) & 0xFF;
    buf[off++] = (chk)      & 0xFF;

    buf[off++] = (pkt->payload_len >> 8) & 0xFF;
    buf[off++] = (pkt->payload_len)      & 0xFF;

    if (pkt->payload_len > 0) {
        memcpy(buf + off, pkt->payload, pkt->payload_len);
        off += pkt->payload_len;
    }

    return (int)off;
}

int replic_unpack(const uint8_t *buf, size_t len, replic_packet_t *pkt) {
    if (buf == NULL || pkt == NULL) return -1;
    if (len < REPLIC_HEADER_SIZE) return -1;

    size_t off = 0;

    pkt->seq_num = ((uint32_t)buf[off]     << 24) |
                   ((uint32_t)buf[off + 1] << 16) |
                   ((uint32_t)buf[off + 2] << 8)  |
                   ((uint32_t)buf[off + 3]);
    off += 4;

    pkt->ack_num = ((uint32_t)buf[off]     << 24) |
                   ((uint32_t)buf[off + 1] << 16) |
                   ((uint32_t)buf[off + 2] << 8)  |
                   ((uint32_t)buf[off + 3]);
    off += 4;

    pkt->flags = buf[off++];

    uint16_t recv_checksum = ((uint16_t)buf[off] << 8) | buf[off + 1];
    off += 2;

    pkt->payload_len = ((uint16_t)buf[off] << 8) | buf[off + 1];
    off += 2;

    if (pkt->payload_len > MAX_PAYLOAD) return -1;
    if (len < (size_t)REPLIC_HEADER_SIZE + pkt->payload_len) return -1;

    if (pkt->payload_len > 0) {
        memcpy(pkt->payload, buf + off, pkt->payload_len);
    }

    uint16_t computed_checksum = replic_checksum(pkt);
    if (computed_checksum != recv_checksum) {
        return -2;
    }

    return 0;
}

void replic_print_packet(const char *label, const replic_packet_t *pkt) {
    printf("[%s] seq=%u ack=%u flags=0x%02X (%s%s%s%s) len=%u\n",
           label,
           pkt->seq_num,
           pkt->ack_num,
           pkt->flags,
           (pkt->flags & FLAG_SYN)  ? "SYN "  : "",
           (pkt->flags & FLAG_ACK)  ? "ACK "  : "",
           (pkt->flags & FLAG_FIN)  ? "FIN "  : "",
           (pkt->flags & FLAG_DATA) ? "DATA " : "",
           pkt->payload_len);
}
