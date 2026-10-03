p = r'<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin'
d = open(p, 'rb').read()

print('bytes 0x144..0x15C:')
for off in range(0x144, 0x15C, 2):
    w = int.from_bytes(d[off:off + 2], 'little')
    print('  0x%04X: %04X' % (off, w))


def decode_ldr_literal(addr, hw):
    """Thumb-1 LDR (literal) T1: 01001 Rt(3) imm8. addr = address of halfword."""
    if (hw & 0xF800) != 0x4800:
        return None
    rt = (hw >> 8) & 0x7
    imm8 = hw & 0xFF
    target = ((addr + 4) & ~3) + imm8 * 4
    return rt, target


for off in (0x014C, 0x0150):
    hw = int.from_bytes(d[off:off + 2], 'little')
    r = decode_ldr_literal(off, hw)
    if r:
        rt, tgt = r
        val = int.from_bytes(d[tgt:tgt + 4], 'little')
        print('0x%04X: LDR r%d, [pc, #%d]  -> literal @0x%04X = 0x%08X'
              % (off, rt, (tgt - ((off + 4) & ~3)) // 4 * 4, tgt, val))

print()
print('literal pool 0x014C..0x015C:')
for off in range(0x014C, 0x015C, 4):
    print('  0x%04X: 0x%08X' % (off, int.from_bytes(d[off:off + 4], 'little')))
