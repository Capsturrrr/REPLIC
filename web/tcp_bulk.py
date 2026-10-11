#!/usr/bin/env python3
"""Continuous kernel-TCP bulk transfer on loopback for the coexistence test.
   python3 web/tcp_bulk.py PORT LOGFILE [PAYLOAD]   - runs until killed (SIGTERM); the receiver appends 'unix_time cumulative_bytes' every 50 ms."""
import socket, sys, threading, time, signal
PORT = int(sys.argv[1]); LOG = sys.argv[2]; SZ = int(sys.argv[3]) if len(sys.argv) > 3 else 512
got = [0]; stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *a: (stop.set(), sys.exit(0)))
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", PORT)); srv.listen(1)
def rx():
    c, _ = srv.accept()
    while not stop.is_set():
        b = c.recv(65536)
        if not b: break
        got[0] += len(b)
def logger():
    with open(LOG, "w") as f:
        while not stop.is_set():
            f.write(f"{time.time():.3f} {got[0]}\n"); f.flush(); time.sleep(0.05)
threading.Thread(target=rx, daemon=True).start(); threading.Thread(target=logger, daemon=True).start()
c = socket.socket(); c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1); c.connect(("127.0.0.1", PORT))
buf = b"x" * SZ
try:
    while not stop.is_set(): c.sendall(buf)
except (BrokenPipeError, ConnectionResetError): pass
