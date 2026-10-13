# UDP relay that drops the FIRST packet the server sends (the SYN-ACK), like a real lossy return path
import socket, sys, select
lp, sp = int(sys.argv[1]), int(sys.argv[2]); n_drop = int(sys.argv[3]) if len(sys.argv) > 3 else 1
a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); a.bind(("127.0.0.1", lp))
b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); b.bind(("127.0.0.1", 0))
client = None; dropped = 0
while True:
    r, _, _ = select.select([a, b], [], [], 30)
    if not r: break
    for s in r:
        d, addr = s.recvfrom(4096)
        if s is a: client = addr; b.sendto(d, ("127.0.0.1", sp))
        else:
            if dropped < n_drop: dropped += 1; continue
            a.sendto(d, client)
