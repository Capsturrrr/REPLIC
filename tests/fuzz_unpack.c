/* Deterministic fuzzer for the wire-format parser: random bytes, truncations and bit-flipped valid packets must never
   crash, over-read or accept a packet whose checksum is wrong. Build with sanitizers: make fuzz */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../src/common.h"

static uint64_t s = 88172645463325252ULL;
static uint32_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return (uint32_t)(s >> 11); }

int main(int argc, char **argv) {
    long iters = argc > 1 ? atol(argv[1]) : 2000000, accepted = 0, rejected = 0, roundtrip = 0;
    uint8_t buf[POISE_HEADER_SIZE + MAX_PAYLOAD + 64], mut[sizeof buf];
    for (long it = 0; it < iters; it++) {
        poise_packet_t p; memset(&p, 0, sizeof p);
        switch (it % 3) {
        case 0: {                                  /* pure noise of random length */
            size_t n = rnd() % sizeof buf; for (size_t i = 0; i < n; i++) buf[i] = (uint8_t)rnd();
            poise_unpack(buf, n, &p) == 0 ? accepted++ : rejected++; break; }
        case 1: {                                  /* valid packet, random truncation */
            poise_packet_t q; memset(&q, 0, sizeof q); q.seq_num = rnd(); q.ack_num = rnd(); q.flags = rnd() & 15;
            q.payload_len = rnd() % (MAX_PAYLOAD + 1); for (int i = 0; i < q.payload_len; i++) q.payload[i] = (uint8_t)rnd();
            int n = poise_pack(&q, buf, sizeof buf); if (n <= 0) { fprintf(stderr, "pack failed\n"); return 1; }
            poise_packet_t r; if (poise_unpack(buf, (size_t)n, &r) != 0 || r.seq_num != q.seq_num || r.payload_len != q.payload_len || memcmp(r.payload, q.payload, q.payload_len)) { fprintf(stderr, "round trip failed\n"); return 1; }
            roundtrip++;
            size_t cut = rnd() % (size_t)n; poise_unpack(buf, cut, &p) == 0 ? accepted++ : rejected++; break; }
        default: {                                 /* valid packet with 1-3 flipped bits must be rejected */
            poise_packet_t q; memset(&q, 0, sizeof q); q.seq_num = rnd(); q.ack_num = rnd(); q.flags = rnd() & 15;
            q.payload_len = rnd() % 64; for (int i = 0; i < q.payload_len; i++) q.payload[i] = (uint8_t)rnd();
            int n = poise_pack(&q, buf, sizeof buf); memcpy(mut, buf, (size_t)n);
            int flips = 1 + rnd() % 3; for (int f = 0; f < flips; f++) mut[rnd() % (size_t)n] ^= (uint8_t)(1u << (rnd() % 8));
            if (memcmp(mut, buf, (size_t)n) == 0) break;
            if (poise_unpack(mut, (size_t)n, &p) == 0) accepted++; else rejected++; }
        }
    }
    printf("fuzz: %ld iterations, %ld round trips ok, %ld rejected, %ld accepted (random hits of the 16-bit checksum)\n", iters, roundtrip, rejected, accepted);
    return 0;
}
