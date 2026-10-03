p = r'<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin'
d = open(p, 'rb').read()

# scan whole image for Thumb-1 LDR-literal halfwords whose resolved literal == 0x19FC
MAGIC = 0x19FC
hits = []
for off in range(0, len(d) - 2, 2):
    hw = int.from_bytes(d[off:off + 2], 'little')
    if (hw & 0xF800) != 0x4800:
        continue
    rt = (hw >> 8) & 7
    base = (off + 4) & ~3
    tgt = base + (hw & 0xFF) * 4
    if tgt + 4 > len(d):
        continue
    val = int.from_bytes(d[tgt:tgt + 4], 'little')
    if val == MAGIC:
        hits.append((off, rt, tgt))

print('LDR-literal references to 0x%05X: %d' % (MAGIC, len(hits)))
for off, rt, tgt in hits:
    print('  0x%05X: ldr r%d, [pc, #%d] -> 0x%05X' % (off, rt, tgt - ((off + 4) & ~3), tgt))

print()
print('--- context of the first executable-looking hit ---')
for off, rt, tgt in hits[:6]:
    lo = max(0, off - 32)
    print('=== hit at 0x%05X (context 0x%05X..0x%05X) ===' % (off, lo, off + 16))
    for a in range(lo, off + 16, 16):
        print('  %06X  %s' % (a, d[a:a + 16].hex(' ')))
    print()

# how many total ldr-literal refs point into the 0x19E0..0x1A10 data area
print('--- all ldr-literal targets landing in 0x19E0..0x1A10 ---')
for off in range(0, len(d) - 2, 2):
    hw = int.from_bytes(d[off:off + 2], 'little')
    if (hw & 0xF800) != 0x4800:
        continue
    base = (off + 4) & ~3
    tgt = base + (hw & 0xFF) * 4
    if 0x19E0 <= tgt <= 0x1A10:
        print('  0x%05X -> literal @0x%05X = 0x%08X'
              % (off, tgt, int.from_bytes(d[tgt:tgt + 4], 'little')))
