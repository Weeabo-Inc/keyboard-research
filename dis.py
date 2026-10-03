p = r'<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin'
d = open(p, 'rb').read()

# Linear Thumb-1 sweep of 0x120..0x160 (approximate; branch targets resolved for B/BX)
def s16(off):
    return int.from_bytes(d[off:off + 2], 'little')


def sx(v, bits):
    if v & (1 << (bits - 1)):
        v -= (1 << bits)
    return v


off = 0x120
while off < 0x160:
    hw = s16(off)
    txt = None
    ln = 2
    if (hw & 0xF800) == 0x4800:
        rt = (hw >> 8) & 7
        tgt = ((off + 4) & ~3) + (hw & 0xFF) * 4
        txt = 'ldr r%d, [pc, #%d]   ; =0x%08X' % (rt, tgt - ((off + 4) & ~3),
                                                  int.from_bytes(d[tgt:tgt + 4], 'little'))
    elif (hw & 0xFF87) == 0x4700:
        rm = (hw >> 3) & 0xF
        txt = 'bx r%d' % rm
    elif (hw & 0xFE00) == 0xB400:
        txt = 'push {%s}' % ','.join('r%d' % i for i in range(8) if hw & (1 << i))
    elif (hw & 0xFE00) == 0xBC00:
        txt = 'pop {%s}' % ','.join('r%d' % i for i in range(8) if hw & (1 << i))
    elif (hw & 0xF800) == 0x6000:
        rt = hw & 7
        rn = (hw >> 3) & 7
        imm = ((hw >> 6) & 0x1F) * 4
        txt = 'str r%d, [r%d, #%d]' % (rt, rn, imm)
    elif (hw & 0xFFC0) == 0x0000:
        txt = 'movs r%d, r%d' % ((hw >> 8) & 7, (hw >> 3) & 7)
    elif (hw & 0xF800) == 0xE000:
        txt = 'b 0x%04X' % (off + 4 + sx(hw & 0x7FF, 11) * 2)
    elif (hw & 0xF000) == 0xD000:
        txt = 'beq/bne 0x%04X (cond %d)' % (off + 4 + sx(hw & 0xFF, 8) * 2, (hw >> 8) & 0xF)
    elif (hw & 0xF800) == 0xF000:
        hw2 = s16(off + 2)
        if (hw2 & 0xD000) == 0xD000:
            s = sx(hw & 0x7FF, 11)
            j2 = (hw2 >> 11) & 1
            j1 = (hw2 >> 13) & 1
            i2 = (hw2 >> 10) & 1
            i1 = (hw2 >> 9) & 1
            imm = (s << 12) | (j1 << 11) | (j2 << 10) | (i1 << 9) | (i2 << 8) | ((hw2 >> 1) & 0x7F)
            imm = sx(imm, 21)
            ln = 4
            txt = 'bl 0x%04X' % (off + 4 + imm)
    elif hw == 0:
        txt = 'movs r0, r0 (nop)'
    if txt is None:
        txt = 'dw 0x%04X' % hw
    print('0x%04X: %-40s %s' % (off, d[off:off + ln].hex(' '), txt))
    off += ln
