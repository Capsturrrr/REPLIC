#!/usr/bin/env python3
"""TCP receiver for the multi-machine test: python3 web/tcp_sink.py [PORT]. For each connection reads until EOF-marker (client half-closes), then replies one byte."""
import socket, sys, threading
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9871
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", PORT)); s.listen(8)
print(f"TCP sink on port {PORT}", flush=True)
def serve(c):
    while c.recv(65536): pass
    c.sendall(b"k"); c.close()
while True:
    c, a = s.accept(); threading.Thread(target=serve, args=(c,), daemon=True).start()
