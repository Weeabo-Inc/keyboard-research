import sys, struct
from collections import Counter
p = sys.argv[1]
d = open(p,'rb').read()

print("=== scan whole file for ARM Cortex-M vector tables ===")
print("(SP in SRAM 0x20000000-0x20010000, reset vector in flash 0x0-0x20000, thumb bit set)")
hits=[]
for off in range(0, len(d)-8, 4):
    sp, rv = struct.unpack_from('<II', d, off)
    if 0x20000000 <= sp <= 0x20010000 and 0x00000000 <= rv < 0x00020000 and (rv & 1):
        hits.append((off, sp, rv))
for off, sp, rv in hits[:20]:
    print("  offset 0x%06X  SP=0x%08X  reset=0x%08X" % (off, sp, rv))
print("  total candidate vector tables:", len(hits))

print()
print("=== entropy map (16KB blocks) - find low-entropy regions that could be code/firmware ===")
BLK=16384
for i in range(0, len(d), BLK):
    blk = d[i:i+BLK]
    if len(blk) < BLK//2: break
    c = Counter(blk)
    distinct = len(c)
    # crude entropy
    import math
    ent = -sum((n/len(blk))*math.log2(n/len(blk)) for n in c.values())
    if distinct < 200:
        print("  0x%06X  distinct=%-4d entropy=%.2f  <-- LOW" % (i, distinct, ent))
