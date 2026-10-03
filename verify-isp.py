import struct

p = r'<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin'
d = open(p, 'rb').read()

print('=== exact decode of 0x014C..0x015C ===')


def ldr_lit(off):
    hw = int.from_bytes(d[off:off + 2], 'little')
    assert (hw & 0xF800) == 0x4800, hex(hw)
    rt = (hw >> 8) & 7
    base = (off + 4) & ~3
    tgt = base + (hw & 0xFF) * 4
    return rt, tgt, int.from_bytes(d[tgt:tgt + 4], 'little')


rt, tgt, val = ldr_lit(0x014C)
print('  0x014C: ldr  r%d, [pc, #%d]  = 0x%08X   (SN_SYS0.IVTM)' % (rt, tgt - 0x0150, val))
hw = int.from_bytes(d[0x014E:0x0150], 'little')
print('  0x014E: %04X -> STR (immediate) T1: 0110 0 imm5 Rn Rt  => str r%d, [r%d, #%d]'
      % (hw, hw & 7, (hw >> 3) & 7, ((hw >> 6) & 0x1F) * 4))
rt, tgt, val = ldr_lit(0x0150)
print('  0x0150: ldr  r%d, [pc, #%d]  = 0x%08X   (boot ROM entry)' % (rt, tgt - 0x0154, val))
hw = int.from_bytes(d[0x0152:0x0154], 'little')
print('  0x0152: %04X -> BX register: 0100 0111 0 Rm(4) 000 => bx r%d' % (hw, (hw >> 3) & 0xF))
print()
print('  => sequence:  *SN_SYS0.IVTM = <const>;  goto 0x1FFF0301;')

print()
print('=== is there a vector table at 0x1FFF0000? (that is boot ROM, not in this image) ===')
print('  image is only user flash 0x00000..0x3FFFF; 0x1FFF0000 is a separate ROM region.')

print()
print('=== IVTM values written: search for consts 0/1 in the immediate vicinity ===')
# Immediately preceding code around 0x0146: bl 0x8065 then pop {r0,r1}
print('  preceding: 0x013E push {r0,r1}; 0x0140 bl ...; 0x0144 pop {r0,r1}; 0x0146 bl ...')
print('  r1 at 0x014C therefore comes from whatever the callee left in r1 (not r0),')
print('  or from the pushed/popped pair. Cannot resolve statically without a full')
print('  decompiler. The safe engineering answer: replicate the documented QMK/ChibiOS')
print('  sequence rather than guess the IVTM constant.')

print()
print('=== sanity: the 8 magic bytes in context ===')
print('  0x19FC:', d[0x19FC:0x1A04].hex(' '), ' -> LE u32: 0x%08X 0x%08X'
      % struct.unpack('<II', d[0x19FC:0x1A04]))
print('  firmware compares:  ldr r1,=0x5A8942AA ; cmp r1,r?   and  ldr r1,=0xCC6271FF ; cmp r1,r?')
