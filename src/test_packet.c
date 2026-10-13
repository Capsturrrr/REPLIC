#include <stdio.h>
#include <string.h>
#include <assert.h>
#include "common.h"

static void test_pack_unpack_roundtrip(void) {
    poise_packet_t original;
    memset(&original, 0, sizeof(original));
    original.seq_num = 42;
    original.ack_num = 7;
    original.flags = FLAG_SYN | FLAG_ACK;
    const char *msg = "hello poise phase 2";
    original.payload_len = (uint16_t)strlen(msg);
    memcpy(original.payload, msg, original.payload_len);

    uint8_t buf[BUFFER_SIZE];
    int packed_len = poise_pack(&original, buf, sizeof(buf));
    assert(packed_len > 0);
    printf("Packed %d bytes\n", packed_len);

    poise_packet_t unpacked;
    memset(&unpacked, 0, sizeof(unpacked));
    int result = poise_unpack(buf, (size_t)packed_len, &unpacked);
    assert(result == 0);

    assert(unpacked.seq_num == original.seq_num);
    assert(unpacked.ack_num == original.ack_num);
    assert(unpacked.flags == original.flags);
    assert(unpacked.payload_len == original.payload_len);
    assert(memcmp(unpacked.payload, original.payload, original.payload_len) == 0);

    poise_print_packet("ORIGINAL", &original);
    poise_print_packet("UNPACKED", &unpacked);

    printf("PASS: pack/unpack round-trip preserves all fields\n\n");
}

static void test_checksum_detects_corruption(void) {
    poise_packet_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.seq_num = 100;
    pkt.ack_num = 0;
    pkt.flags = FLAG_DATA;
    const char *msg = "data payload";
    pkt.payload_len = (uint16_t)strlen(msg);
    memcpy(pkt.payload, msg, pkt.payload_len);

    uint8_t buf[BUFFER_SIZE];
    int packed_len = poise_pack(&pkt, buf, sizeof(buf));
    assert(packed_len > 0);

    /* Flip a bit in the payload to simulate corruption in transit */
    buf[packed_len - 1] ^= 0xFF;

    poise_packet_t corrupted;
    int result = poise_unpack(buf, (size_t)packed_len, &corrupted);
    assert(result == -2); /* checksum mismatch expected */

    printf("PASS: checksum correctly detects corrupted payload\n\n");
}

static void test_empty_payload(void) {
    poise_packet_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    pkt.seq_num = 1;
    pkt.ack_num = 1;
    pkt.flags = FLAG_ACK;
    pkt.payload_len = 0;

    uint8_t buf[BUFFER_SIZE];
    int packed_len = poise_pack(&pkt, buf, sizeof(buf));
    assert(packed_len == POISE_HEADER_SIZE);

    poise_packet_t unpacked;
    int result = poise_unpack(buf, (size_t)packed_len, &unpacked);
    assert(result == 0);
    assert(unpacked.payload_len == 0);

    printf("PASS: pure ACK/control packet with empty payload works\n\n");
}

int main(void) {
    printf("=== Poise Phase 2: Packet Format Tests ===\n\n");
    test_pack_unpack_roundtrip();
    test_checksum_detects_corruption();
    test_empty_payload();
    printf("=== All tests passed ===\n");
    return 0;
}
