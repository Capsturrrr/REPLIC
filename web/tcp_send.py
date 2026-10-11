#!/usr/bin/env python3
"""TCP sender for the multi-machine test: python3 web/tcp_send.py HOST PORT BYTES  -> prints seconds until the receiver confirms everything arrived."""
import socket, sys, time
host, port, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
c = socket.socket(); c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1); c.settimeout(120)
t0 = time.time(); c.connect((host, port)); buf = b"x" * 512
for _ in range(n // 512): c.sendall(buf)
c.shutdown(socket.SHUT_WR); c.recv(1); print(f"{time.time()-t0:.3f}")
