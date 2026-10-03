import re
d = open(r'<HOME>\Downloads\S98PRO Firemware.exe','rb').read()

print("=== Is 0x1BAA4C (the 'SN32F290.hex' area) inside a section? ===")
import struct
pe=struct.unpack_from('<I',d,0x3C)[0]
nsec=struct.unpack_from('<H',d,pe+6)[0]
optsz=struct.unpack_from('<H',d,pe+20)[0]
so=pe+24+optsz
secs=[]
for i in range(nsec):
    o=so+i*40
    nm=d[o:o+8].rstrip(b'\0').decode('latin1')
    va=struct.unpack_from('<I',d,o+12)[0]
    vs=struct.unpack_from('<I',d,o+8)[0]
    rp=struct.unpack_from('<I',d,o+20)[0]
    rs=struct.unpack_from('<I',d,o+16)[0]
    secs.append((nm,va,vs,rp,rs,o))
    print("  %-8s raw file range 0x%X..0x%X" % (nm, rp, rp+rs))
for off in (0x1BB164, 0x1BAA4C, 0x1B0FC4):
    for nm,va,vs,rp,rs,o in secs:
        if rp <= off < rp+rs:
            print("  0x%X is in %s (RVA 0x%X)" % (off, nm, va + (off-rp)))
            break
    else:
        print("  0x%X is NOT in any section" % off)

print()
print("=== strings near the HFD magic (0x1BB164) - what is this payload? ===")
lo, hi = 0x1BAA00, 0x1BC400
seg = d[lo:hi]
for m in re.finditer(rb'[\x20-\x7e]{5,}', seg):
    print("  0x%06X  %s" % (lo+m.start(), m.group().decode('latin1')))

print()
print("=== ISP-related strings with their file offsets (the tool's logic) ===")
for pat in (rb'Check ISP Password', rb'Check Chip Name', rb'Check Build Time', rb'SNX_Find_HID',
            rb'Find Device', rb'Check if connected device is SONiX ISP', rb'Get the firmware version',
            rb'This firmware version is not supported', rb'Enter ISP', rb'ISP Mode', rb'Jump'):
    for m in re.finditer(re.escape(pat), d):
        print("  0x%06X  %s" % (m.start(), pat.decode()))
