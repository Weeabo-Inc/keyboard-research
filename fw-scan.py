import struct

p = r'<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin'
d = open(p, 'rb').read()


def hx(a, n):
    print('--- 0x%04X ---' % a)
    for off in range(a, a + n, 16):
        b = d[off:off + 16]
        print('%06X  %s' % (off, b.hex(' ')))


for a in (0x140, 0x1A0, 0x1F60, 0x1A10):
    hx(a, 0x40)

print()
print('nonzero-up-to (last non-FF offset per 4K block):')
for blk in range(64):
    lo = blk * 4096
    seg = d[lo:lo + 4096]
    last = -1
    for i in range(len(seg) - 1, -1, -1):
        if seg[i] != 0xFF:
            last = i
            break
    if last >= 0:
        print('  0x%05X..0x%05X used' % (lo, lo + last))

print()
print('total non-FF bytes: %d of %d' % (sum(1 for b in d if b != 0xFF), len(d)))
