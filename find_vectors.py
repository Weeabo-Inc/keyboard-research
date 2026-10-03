import sys, struct
p = sys.argv[1]
d = open(p,'rb').read()

def is_handler(v):
    # thumb code pointer in the low flash window
    return (v & 1) == 1 and 0x100 <= v < 0x20000

print("=== looking for a REAL vector table: SP + a RUN of plausible handlers ===")
best = []
for off in range(0, len(d)-64, 4):
    sp = struct.unpack_from('<I', d, off)[0]
    if not (0x20000000 <= sp <= 0x20010000):
        continue
    if sp % 4 != 0:
        continue
    # count consecutive plausible handlers
    run = 0
    for k in range(1, 20):
        if off + k*4 + 4 > len(d): break
        v = struct.unpack_from('<I', d, off + k*4)[0]
        if is_handler(v):
            run += 1
        else:
            break
    if run >= 6:
        best.append((run, off, sp))
best.sort(reverse=True)
print("candidates with >=6 consecutive handler-like words:")
for run, off, sp in best[:15]:
    print("  offset 0x%06X  SP=0x%08X  run=%d" % (off, sp, run))

if best:
    run, off, sp = best[0]
    print()
    print("=== BEST CANDIDATE at 0x%06X (run=%d, SP=0x%08X) ===" % (off, run, sp))
    print("first 32 vectors:")
    for k in range(32):
        v = struct.unpack_from('<I', d, off + k*4)[0]
        print("   [%2d] 0x%08X %s" % (k, v, "<- handler" if is_handler(v) else ""))
    # assume flash base 0 and dump the next 64KB as a candidate firmware image
    fw = d[off:off+65536]
    out = sys.argv[2]
    open(out,'wb').write(fw)
    print()
    print("wrote 64KB candidate firmware ->", out)
    print("first 16 bytes:", fw[:16].hex())
    # sanity: is it mostly non-zero
    nz = sum(1 for b in fw if b != 0)
    print("non-zero bytes in 64KB: %d (%.1f%%)" % (nz, 100.0*nz/len(fw)))
