import sys, struct, os
from collections import Counter
import math
p = sys.argv[1]
outdir = sys.argv[2]
os.makedirs(outdir, exist_ok=True)
d = open(p,'rb').read()

pe_off = struct.unpack_from('<I', d, 0x3C)[0]
nsec = struct.unpack_from('<H', d, pe_off+6)[0]
opt_size = struct.unpack_from('<H', d, pe_off+20)[0]
sec_off = pe_off + 24 + opt_size
secs = []
for i in range(nsec):
    o = sec_off + i*40
    name = d[o:o+8].rstrip(b'\0').decode('latin1')
    vsize = struct.unpack_from('<I', d, o+8)[0]
    va    = struct.unpack_from('<I', d, o+12)[0]
    rawsize = struct.unpack_from('<I', d, o+16)[0]
    rawptr  = struct.unpack_from('<I', d, o+20)[0]
    secs.append((name, va, vsize, rawptr, rawsize))
    print("  %-8s VA=0x%08X vsize=%-8d rawptr=0x%X rawsize=%d" % (name, va, vsize, rawptr, rawsize))

def rva2off(rva):
    for name, va, vsize, rawptr, rawsize in secs:
        if va <= rva < va + max(vsize, rawsize):
            return rawptr + (rva - va)
    return None

blobs = [(0x1C564C, 262144, "blob_256k.bin"), (0x20567C, 145091, "blob_142k.bin")]
for rva, size, name in blobs:
    off = rva2off(rva)
    if off is None:
        print("RVA 0x%X not mapped!" % rva); continue
    data = d[off:off+size]
    out = os.path.join(outdir, name)
    open(out,'wb').write(data)
    c = Counter(data)
    ent = -sum((n/len(data))*math.log2(n/len(data)) for n in c.values())
    nz = sum(1 for b in data if b)
    print()
    print("=== %s : %d bytes ===" % (name, len(data)))
    print("  distinct byte values : %d/256" % len(c))
    print("  entropy              : %.3f bits/byte (8.0 = random/encrypted)" % ent)
    print("  non-zero             : %d (%.1f%%)" % (nz, 100.0*nz/len(data)))
    print("  most common          : %s" % c.most_common(4))
    print("  first 32 bytes       : %s" % data[:32].hex())
    # is there any vector table anywhere inside?
    found=[]
    for o in range(0, len(data)-64, 4):
        sp = struct.unpack_from('<I', data, o)[0]
        if 0x20000000 <= sp <= 0x20020000 and sp % 4 == 0:
            run = 0
            for k in range(1,24):
                if o+k*4+4 > len(data): break
                v = struct.unpack_from('<I', data, o+k*4)[0]
                if (v & 1) and 0x100 <= v < 0x40000: run += 1
                else: break
            if run >= 4: found.append((o, sp, run))
    print("  vector-table candidates: %s" % (found[:6] if found else "NONE"))
    print("  -> %s" % out)
