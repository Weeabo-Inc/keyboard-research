import re, struct
p = r'<HOME>\Downloads\S98PRO Firemware.exe'
d = open(p,'rb').read()
print("size: %d" % len(d))
print()
print("=== every occurrence of the ISP magic values (as u32 LE) ===")
for name, val in (("0x5AA555AA",0x5AA555AA), ("0xCC3300FF",0xCC3300FF),
                  ("0x5A8942AA",0x5A8942AA), ("0xCC6271FF",0xCC6271FF),
                  ("0xAA55A55A",0xAA55A55A), ("0xAA5542AA",0xAA5542AA)):
    pat = val.to_bytes(4,'little')
    offs = [m.start() for m in re.finditer(re.escape(pat), d)]
    if offs:
        print("  %s (bytes %s): %d occurrence(s) at %s" % (name, pat.hex(), len(offs),
              ', '.join('0x%X'%o for o in offs[:10])))
    else:
        print("  %s : none" % name)
print()
print("=== the 8-byte CheckDeviceCmd sequences ===")
for name, hx in (("ini AA55A55AFF0033CC","AA55A55AFF0033CC"),
                 ("guide AA42895AFF7162CC","AA42895AFF7162CC")):
    pat = bytes.fromhex(hx)
    offs = [m.start() for m in re.finditer(re.escape(pat), d)]
    print("  %-24s : %d occurrence(s) %s" % (name, len(offs), [hex(o) for o in offs[:8]]))
print()
print("=== ISP command bytes referenced directly (near 'ISP_Command') ===")
for m in re.finditer(rb'ISP_Command', d):
    o = m.start()
    print("  'ISP_Command' at 0x%X  context: %s" % (o, d[max(0,o-32):o+40].hex()))
print()
print("=== all short ASCII hex-looking tokens >= 8 chars ===")
toks = re.findall(rb'[0-9A-F]{8,32}', d)
seen=[]
for t in toks:
    s=t.decode()
    if s not in seen: seen.append(s)
for s in seen[:50]:
    print("   ", s)
print("   total unique:", len(seen))
