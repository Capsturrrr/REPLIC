-- REPLIC dissector for Wireshark (protocol name "replic"). Install: copy to the Wireshark plugins folder
-- (Help > About Wireshark > Folders > Personal Lua Plugins) and restart, or:
--   wireshark -X lua_script:replic.lua capture.pcap
-- Header (13 bytes): seq(4) ack(4) flags(1) checksum(2) payload_len(2)
local p = Proto("replic", "Reliable UDP (course project)")
local f = {
  seq   = ProtoField.uint32("replic.seq", "Sequence number"),
  ack   = ProtoField.uint32("replic.ack", "Acknowledgement number"),
  flags = ProtoField.uint8("replic.flags", "Flags", base.HEX),
  syn   = ProtoField.bool("replic.flags.syn", "SYN", 8, nil, 0x01),
  ackf  = ProtoField.bool("replic.flags.ack", "ACK", 8, nil, 0x02),
  fin   = ProtoField.bool("replic.flags.fin", "FIN", 8, nil, 0x04),
  data  = ProtoField.bool("replic.flags.data", "DATA", 8, nil, 0x08),
  csum  = ProtoField.uint16("replic.checksum", "Checksum", base.HEX),
  plen  = ProtoField.uint16("replic.payload_len", "Payload length"),
  pl    = ProtoField.bytes("replic.payload", "Payload"),
}
p.fields = { f.seq, f.ack, f.flags, f.syn, f.ackf, f.fin, f.data, f.csum, f.plen, f.pl }
function p.dissector(buf, pinfo, tree)
  if buf:len() < 13 then return 0 end
  pinfo.cols.protocol = "REPLIC"
  local t = tree:add(p, buf(0, buf:len()), "REPLIC")
  t:add(f.seq, buf(0, 4)); t:add(f.ack, buf(4, 4))
  local ft = t:add(f.flags, buf(8, 1))
  ft:add(f.syn, buf(8, 1)); ft:add(f.ackf, buf(8, 1)); ft:add(f.fin, buf(8, 1)); ft:add(f.data, buf(8, 1))
  t:add(f.csum, buf(9, 2)); t:add(f.plen, buf(11, 2))
  local pl = buf(11, 2):uint()
  if pl > 0 and buf:len() >= 13 + pl then t:add(f.pl, buf(13, pl)) end
  local fl = buf(8, 1):uint(); local n = {}
  if fl % 2 == 1 then n[#n+1] = "SYN" end
  if math.floor(fl / 2) % 2 == 1 then n[#n+1] = "ACK" end
  if math.floor(fl / 4) % 2 == 1 then n[#n+1] = "FIN" end
  if math.floor(fl / 8) % 2 == 1 then n[#n+1] = "DATA" end
  pinfo.cols.info = string.format("[%s] seq=%u ack=%u len=%d", table.concat(n, ","), buf(0,4):uint(), buf(4,4):uint(), pl)
  return buf:len()
end
local udp = DissectorTable.get("udp.port")
for _, port in ipairs({8888, 40000, 9101, 9102, 9103}) do udp:add(port, p) end
