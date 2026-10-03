import struct, os
d = open(r'<HOME>\Downloads\S98PRO Firemware.exe','rb').read()
out = r'<REPO_ROOT>\keyboard\sonix-tool\embedded'
os.makedirs(out, exist_ok=True)

# Candidate: ARM image starting at the vector table we found at 0x1BAA4C
VT = 0x1BAA4C
sp, rv = struct.unpack_from('<II', d, VT)
print("vector table @0x%X  SP=0x%08X  reset=0x%08X" % (VT, sp, rv))

# find the extent: last non-FF byte after VT
end = VT
for i in range(VT, min(VT+0x20000, len(d))):
    if d[i] != 0xFF:
        end = i
img = d[VT:end+1]
p = os.path.join(out, 'embedded_image.bin')
open(p,'wb').write(img)
nz = sum(1 for b in img if b)
print("extracted %d bytes -> %s  (%d non-zero, %.1f%%)" % (len(img), p, nz, 100.0*nz/len(img)))

# where does the HFD magic sit relative to the image?
magic = bytes.fromhex('AA42895AFF7162CC')
mo = d.find(magic)
print("HFD magic at 0x%X = image offset 0x%X" % (mo, mo - VT))

# ALL vector-table-like spots in .rsrc
print()
print("=== all ARM vector tables in the resource section (0x1B1000..0x21D400) ===")
found=[]
for o in range(0x1B1000, 0x21D400-64, 4):
    s = struct.unpack_from('<I', d, o)[0]
    if not (0x20000000 <= s <= 0x20020000) or s % 4: continue
    run=0
    for k in range(1,24):
        v = struct.unpack_from('<I', d, o+k*4)[0]
        if (v & 1) and 0x100 <= v < 0x40000: run+=1
        else: break
    if run >= 5: found.append((o, s, rv if o==VT else struct.unpack_from('<I',d,o+4)[0], run))
for o,s,r,run in found:
    print("  0x%06X  SP=0x%08X reset=0x%08X run=%d" % (o,s,r,run))
print("  total:", len(found))
