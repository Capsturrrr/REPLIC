#!/usr/bin/env python3
"""Regression test: the client's final handshake ACK is lost, so the first thing the server sees after its SYN+ACK is DATA.
The server must treat that as completing the handshake and ACK the data (before the fix the connection hung forever)."""
import socket, struct, subprocess, sys, time, os

def crc(data):
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8): c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c

def pack(seq, ack, flags, payload=b""):
    body = struct.pack(">IIBH", seq, ack, flags, len(payload)) + payload
    return struct.pack(">IIBHH", seq, ack, flags, crc(body), len(payload)) + payload

def unpack(b):
    seq, ack, flags, ck, n = struct.unpack(">IIBHH", b[:13]); return seq, ack, flags, b[13:13 + n]

port = 9871
root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
subprocess.run(["gcc", f"-DSERVER_PORT={port}", "-o", "/tmp/server_hs", os.path.join(root, "src/server.c"), os.path.join(root, "src/common.c")], check=True)
srv = subprocess.Popen(["/tmp/server_hs"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ, REPLIC_QUIET="1"))
time.sleep(0.4)
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2)
    s.sendto(pack(1000, 0, 0x01), ("127.0.0.1", port))
    seq, ack, flags, _ = unpack(s.recvfrom(2048)[0])
    assert flags == 0x03 and ack == 1001, "expected SYN+ACK"
    # (the final ACK is "lost": we do not send it) -> straight to data
    s.sendto(pack(1001, 0, 0x08, b"hello"), ("127.0.0.1", port))
    seq, ack, flags, _ = unpack(s.recvfrom(2048)[0])
    assert flags & 0x02 and ack == 1002, f"data was not acknowledged (flags={flags:#x} ack={ack})"
    print("ok:   data after a lost handshake ACK completes the handshake and is acknowledged")
except Exception as e:
    print("FAIL:", e); sys.exit(1)
finally:
    srv.kill()
