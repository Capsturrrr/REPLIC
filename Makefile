CC := gcc
CFLAGS := -Wall -Wextra -g -std=c11
SRC_DIR := src
BIN_DIR := bin

COMMON_SRC := $(SRC_DIR)/common.c

all: $(BIN_DIR)/server $(BIN_DIR)/client

$(BIN_DIR):
	mkdir -p $(BIN_DIR)

$(BIN_DIR)/server: $(SRC_DIR)/server.c $(COMMON_SRC) | $(BIN_DIR)
	$(CC) $(CFLAGS) -o $@ $^

$(BIN_DIR)/client: $(SRC_DIR)/client.c $(COMMON_SRC) | $(BIN_DIR)
	$(CC) $(CFLAGS) -o $@ $^

clean:
	rm -rf $(BIN_DIR) lib build

.PHONY: all clean

# Poise (RL congestion control) -- needs -lm; server is built per port
$(BIN_DIR)/smart_client: $(SRC_DIR)/smart_client.c $(SRC_DIR)/poise_sender.c $(SRC_DIR)/poise.h $(SRC_DIR)/rl_cc.h $(SRC_DIR)/deep_cc.h $(COMMON_SRC) | $(BIN_DIR)
	$(CC) $(CFLAGS) -D_GNU_SOURCE -o $@ $(SRC_DIR)/smart_client.c $(SRC_DIR)/poise_sender.c $(COMMON_SRC) -lm

smart: $(BIN_DIR)/server $(BIN_DIR)/smart_client
.PHONY: smart

# Poise Chat demo (two-way messenger over Poise packets)
$(BIN_DIR)/chat: $(SRC_DIR)/chat.c $(COMMON_SRC) | $(BIN_DIR)
	$(CC) $(CFLAGS) -o $@ $^

chat: $(BIN_DIR)/chat
.PHONY: chat

# Selective Repeat is now the default (server buffers out-of-order packets and reports them in an ACK bitmap).
# The old Go-Back-N receiver is kept for comparison: make server_gbn  (pair it with: smart_client --gbn)
$(BIN_DIR)/server_gbn: $(SRC_DIR)/server.c $(COMMON_SRC) | $(BIN_DIR)
	$(CC) $(CFLAGS) -DPOISE_GBN -o $@ $(SRC_DIR)/server.c $(COMMON_SRC)

gbn: $(BIN_DIR)/server_gbn $(BIN_DIR)/smart_client
.PHONY: gbn

# backward-compatible alias from the time Selective ACK was opt-in
sack: smart
.PHONY: sack

# Run the whole test suite (wire format, end to end, transport modes, simulator)
test:
	bash tests/run_tests.sh
.PHONY: test

# Quick tour: build everything, run the tests, print how to start the demos
demo: test
	@echo; echo 'Demos: see docs/CHAT_ONLINE.txt and docs/POISE_Demo_Runbook.html'
.PHONY: demo

# Sanitizer build + fuzzer for the parser:  make fuzz
fuzz:
	mkdir -p $(BIN_DIR)
	$(CC) -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all -o $(BIN_DIR)/fuzz_unpack tests/fuzz_unpack.c $(COMMON_SRC)
	$(BIN_DIR)/fuzz_unpack 1000000
.PHONY: fuzz

# User-space UDP impairment proxy (delay / jitter / loss / bottleneck rate + drop-tail queue) for tests over real sockets
$(BIN_DIR)/impair: $(SRC_DIR)/impair.c | $(BIN_DIR)
	$(CC) $(CFLAGS) -O2 -o $@ $< -lm
impair: $(BIN_DIR)/impair
.PHONY: impair

# Reproduce the headline result in virtual time (SEEDS=100 by default; NOTEST=1 skips the test suite)
drop: smart impair
	python3 apps/drop/app.py

repro:
	./repro.sh
.PHONY: repro

# Regenerate headline figures into figures/ (about a minute)
figures:
	python3 web/make_figures.py
.PHONY: figures

# ---- libpoise: the sender as a static library with a small public API (src/poise.h) ----
PREFIX ?= /usr/local
lib/libpoise.a: $(SRC_DIR)/poise_sender.c $(SRC_DIR)/common.c $(SRC_DIR)/poise.h $(SRC_DIR)/common.h $(SRC_DIR)/rl_cc.h $(SRC_DIR)/deep_cc.h
	@mkdir -p lib build
	$(CC) $(CFLAGS) -D_GNU_SOURCE -c -o build/poise_sender.o $(SRC_DIR)/poise_sender.c
	$(CC) $(CFLAGS) -c -o build/common.o $(SRC_DIR)/common.c
	ar rcs $@ build/poise_sender.o build/common.o
lib: lib/libpoise.a
$(BIN_DIR)/example_send: examples/send_buffer.c lib/libpoise.a | $(BIN_DIR)
	$(CC) $(CFLAGS) -I$(SRC_DIR) -o $@ examples/send_buffer.c lib/libpoise.a -lm
examples: $(BIN_DIR)/example_send
$(BIN_DIR)/test_api: tests/test_api.c lib/libpoise.a | $(BIN_DIR)
	$(CC) $(CFLAGS) -I$(SRC_DIR) -o $@ tests/test_api.c lib/libpoise.a -lm
install: lib/libpoise.a $(BIN_DIR)/server $(BIN_DIR)/smart_client
	install -d $(DESTDIR)$(PREFIX)/lib $(DESTDIR)$(PREFIX)/include $(DESTDIR)$(PREFIX)/bin
	install -m 644 lib/libpoise.a $(DESTDIR)$(PREFIX)/lib/
	install -m 644 $(SRC_DIR)/poise.h $(DESTDIR)$(PREFIX)/include/
	install -m 755 $(BIN_DIR)/server $(DESTDIR)$(PREFIX)/bin/poise-server
	install -m 755 $(BIN_DIR)/smart_client $(DESTDIR)$(PREFIX)/bin/poise-send
.PHONY: lib examples install
