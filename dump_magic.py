d = open(r'<HOME>\Downloads\S98PRO Firemware.exe','rb').read()

def dump(off, before, after, label):
    s = max(0, off-before); e = min(len(d), off+after)
    print("=== %s @ 0x%X ===" % (label, off))
    for row in range(s, e, 16):
        chunk = d[row:row+16]
        asc = ''.join(chr(b) if 32<=b<127 else '.' for b in chunk)
        mark = ' <-- ' if row <= off < row+16 else ''
        print("  0x%06X  %-47s  |%s|%s" % (row, ' '.join('%02X'%b for b in chunk), asc, mark))
    print()

dump(0x1BB164, 48, 112, "HFD magic pair")
dump(0x1B6816, 48, 96, "Sonix magic pair (0xCC3300FF)")
print("=== 0x1B6816 region: is 0x5AA555AA stored slightly differently? ===")
print("  bytes 0x1B6800-0x1B6850:", d[0x1B6800:0x1B6850].hex())
print()
print("=== search for 5A A5 55 AA in any arrangement ===")
import re
for pat,name in ((b'\x5a\xa5\x55\xaa','5AA555AA'), (b'\xaa\x55\xa5\x5a','AA55A55A'),
                 (b'\x5a\xa5\x55','5AA555'), (b'\xaa\x55\xa5','AA55A5')):
    offs=[m.start() for m in re.finditer(re.escape(pat), d)]
    print("  %-10s : %d  %s" % (name, len(offs), [hex(o) for o in offs[:6]]))
