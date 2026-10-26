#!/usr/bin/env python3
"""Real kernel TCP and plain UDP on loopback, for use with tc/netem (see scripts/netem_baselines.sh).
   python3 web/real_baselines.py [packets] [payload_bytes]
   TCP: sends N payloads over one connection, time until the receiver has them all.
   UDP: sends N datagrams once, waits 1 s after the last, reports how many arrived (no retransmission)."""
import socket, sys, threading, time
N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
SZ = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
PORT_T, PORT_U = 9851, 9852

def tcp():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT_T)); srv.listen(1); done = {}
    def serve():
        c, _ = srv.accept(); got = 0
        while got < N * SZ:
            b = c.recv(65536)
            if not b: break
            got += len(b)
        done["t"] = time.time(); done["got"] = got; c.close()
    th = threading.Thread(target=serve); th.start()
    c = socket.socket(); c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    t0 = time.time(); c.connect(("127.0.0.1", PORT_T))
    for _ in range(N): c.sendall(b"x" * SZ)
    th.join(60); c.close(); srv.close()
    return done.get("t", time.time()) - t0, done.get("got", 0) // SZ

def udp():
    r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); r.bind(("127.0.0.1", PORT_U)); r.settimeout(1.0)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); got = [0]
    def rx():
        try:
            while True: r.recv(4096); got[0] += 1
        except socket.timeout: pass
    th = threading.Thread(target=rx); th.start(); t0 = time.time()
    for i in range(N): s.sendto(b"x" * SZ, ("127.0.0.1", PORT_U)); 
    th.join(30); return time.time() - t0 - 1.0, got[0]

if __name__ == "__main__":
    t, g = tcp(); print(f"TCP  {g}/{N} packets in {t:.2f} s")
    t, g = udp(); print(f"UDP  {g}/{N} packets ({100*g/N:.0f}% delivered, no retransmission)")
