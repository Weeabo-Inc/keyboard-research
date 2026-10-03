import sys, os, struct, glob

def find_vectors(d):
    """Find the best ARM Cortex-M vector table."""
    best = None
    for off in range(0, len(d)-64, 4):
        sp = struct.unpack_from('<I', d, off)[0]
        if not (0x20000000 <= sp <= 0x20020000) or sp % 4: continue
        run = 0
        for k in range(1, 24):
            if off+k*4+4 > len(d): break
            v = struct.unpack_from('<I', d, off+k*4)[0]
            if (v & 1) and 0x100 <= v < 0x40000: run += 1
            else: break
        if run >= 6 and (best is None or run > best[0]):
            best = (run, off, sp)
    return best

for p in sorted(glob.glob(os.path.join(sys.argv[1], "*.exe"))):
    d = open(p,'rb').read()
    b = find_vectors(d)
    name = os.path.basename(p)
    if b:
        run, off, sp = b
        img = d[off:off+0x10000]
        nz = sum(1 for x in img if x)
        print("%-58s VT=0x%06X SP=0x%08X run=%-2d nonZero=%d%%" % (name[:58], off, sp, run, 100*nz//len(img)))
    else:
        print("%-58s NO VECTOR TABLE FOUND" % name[:58])
