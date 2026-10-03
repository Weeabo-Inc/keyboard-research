#!/usr/bin/env python3
"""
ARMv6-M (Thumb-1 + BL/BLX) decoder + linear sweep for Cortex-M0 firmware.

Encodings follow ARM DDI 0419 (ARMv6-M ARM), section A6.3.
READ-ONLY helper.

  python thumbdis.py IMAGE.bin dis  <start> <end>
  python thumbdis.py IMAGE.bin gpio
  python thumbdis.py IMAGE.bin sweep <outfile>
"""
import struct
import sys

R = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
     "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]

GPIO_BASES = {0x40044000: "GPIO0", 0x40046000: "GPIO1",
              0x40048000: "GPIO2", 0x4004A000: "GPIO3"}

GPIO_REG = {0x00: "DATA", 0x04: "MODE", 0x08: "CFG", 0x0C: "IS",
            0x10: "IEV", 0x14: "IE", 0x18: "RIS", 0x1C: "MIS",
            0x20: "IC", 0x24: "SET", 0x28: "CLR", 0x30: "CFG1"}


def sx(v, bits):
    m = 1 << (bits - 1)
    return (v ^ m) - m


class Ins:
    __slots__ = ("addr", "hw", "text", "mem", "base", "imm", "lit", "kind")

    def __init__(self, addr, hw):
        self.addr = addr
        self.hw = hw
        self.text = "??? 0x%04x" % hw
        self.mem = False
        self.base = -1        # 0..7 register, -2 = SP, -3 = PC
        self.imm = None
        self.lit = None       # absolute literal-pool address for PC-rel load
        self.kind = ""


def decode(hw, addr):
    i = Ins(addr, hw)
    t = hw >> 12
    b15_11 = hw >> 11          # 5 bits
    b15_10 = hw >> 10          # 6 bits
    b15_13 = hw >> 13          # 3 bits
    b15_12 = hw >> 12          # 4 bits
    b15_9 = hw >> 9
    b15_6 = hw >> 6

    # fmt 1  LSL/LSR/ASR immediate        bits15:13 == 000
    if b15_13 == 0b000:
        sub = (hw >> 11) & 3
        imm = (hw >> 6) & 0x1F
        rd, rs = hw & 7, (hw >> 3) & 7
        if sub == 0:
            i.text = "lsls %s, %s, #%d" % (R[rd], R[rs], imm)
        elif sub == 1:
            i.text = "lsrs %s, %s, #%d" % (R[rd], R[rs], imm or 32)
        else:
            i.text = "asrs %s, %s, #%d" % (R[rd], R[rs], imm or 32)
        return i
    # fmt 2  ADD/SUB (reg or imm3)        bits15:11 == 00011
    if b15_11 == 0b00011:
        immf = (hw >> 10) & 1
        sub = (hw >> 9) & 1
        rn, rs, rd = (hw >> 6) & 7, (hw >> 3) & 7, hw & 7
        i.text = "%s %s, %s, %s" % ("subs" if sub else "adds", R[rd], R[rs],
                                    ("#%d" % rn) if immf else R[rn])
        return i
    # fmt 3  MOV/CMP/ADD/SUB imm8         bits15:13 == 001
    if b15_13 == 0b001:
        sub = (hw >> 11) & 3
        rd, imm = (hw >> 8) & 7, hw & 0xFF
        i.text = "%s %s, #%d" % (["movs", "cmp", "adds", "subs"][sub], R[rd], imm)
        return i
    # fmt 4  data-processing register     bits15:10 == 010000
    if b15_10 == 0b010000:
        sub = (hw >> 6) & 0xF
        rs, rd = (hw >> 3) & 7, hw & 7
        names = ["ands", "eors", "lsls", "lsrs", "asrs", "adcs", "sbcs", "rors",
                 "tst", "rsbs", "cmp", "cmn", "orrs", "muls", "bics", "mvns"]
        i.text = "%s %s, %s" % (names[sub], R[rd], R[rs])
        return i
    # fmt 5  special data / BX / BLX      bits15:10 == 010001
    if b15_10 == 0b010001:
        sub = (hw >> 8) & 3
        rs = (((hw >> 6) & 1) << 3) | ((hw >> 3) & 7)
        rd = (((hw >> 7) & 1) << 3) | (hw & 7)
        if sub == 0:
            i.text = "add %s, %s" % (R[rd], R[rs])
        elif sub == 1:
            i.text = "cmp %s, %s" % (R[rd], R[rs])
        elif sub == 2:
            i.text = "mov %s, %s" % (R[rd], R[rs])
        else:
            i.text = ("blx %s" if (hw >> 7) & 1 else "bx %s") % R[rs]
            if rs in (14, 15):
                i.kind = "bx"
        return i
    # fmt 6  LDR (literal)                bits15:11 == 01001
    if b15_11 == 0b01001:
        rd = (hw >> 8) & 7
        imm = (hw & 0xFF) << 2
        i.text = "ldr %s, [pc, #%d]" % (R[rd], imm)
        i.mem, i.base, i.kind = True, -3, "ldr"
        i.imm = ((addr + 4) & ~3) + imm
        i.lit = i.imm
        return i
    # fmt 7/8 load/store                        bits15:12 in 0101/0110/0111
    if b15_12 == 0b0101:
        # fmt 7 register offset          bits15:12 == 0101, bits11:9 != 000
        if ((hw >> 9) & 7) != 0:
            sub = (hw >> 9) & 7
            rm, rn, rd = (hw >> 6) & 7, (hw >> 3) & 7, hw & 7
            nm = ["str", "strh", "strb", "ldrsb", "ldr", "ldrh", "ldrb", "ldrsh"][sub]
            i.text = "%s %s, [%s, %s]" % (nm, R[rd], R[rn], R[rm])
            i.mem, i.base, i.kind = True, rn, nm
            return i
        # fmt 9 word immediate           bits15:12 == 0101, bits11:9 == 000
        l = (hw >> 11) & 1
        imm5 = (hw >> 6) & 0x1F
        rn, rd = (hw >> 3) & 7, hw & 7
        nm = "ldr" if l else "str"
        i.text = "%s %s, [%s, #%d]" % (nm, R[rd], R[rn], imm5 << 2)
        i.mem, i.base, i.imm, i.kind = True, rn, imm5 << 2, nm
        return i
    if b15_12 == 0b0110 or b15_12 == 0b0111:
        # fmt 10 halfword imm             bits15:12 == 1000 -> not here
        if b15_12 == 0b0110:
            l = (hw >> 11) & 1
            imm5 = (hw >> 6) & 0x1F
            rn, rd = (hw >> 3) & 7, hw & 7
            nm = "ldrh" if l else "strh"
            i.text = "%s %s, [%s, #%d]" % (nm, R[rd], R[rn], imm5 << 1)
            i.mem, i.base, i.imm, i.kind = True, rn, imm5 << 1, nm
            return i
        # fmt 8 byte imm                  bits15:12 == 0111
        l = (hw >> 11) & 1
        imm5 = (hw >> 6) & 0x1F
        rn, rd = (hw >> 3) & 7, hw & 7
        nm = "ldrb" if l else "strb"
        i.text = "%s %s, [%s, #%d]" % (nm, R[rd], R[rn], imm5)
        i.mem, i.base, i.imm, i.kind = True, rn, imm5, nm
        return i
    if b15_12 == 0b1000:
        # fmt 10 halfword immediate
        l = (hw >> 11) & 1
        imm5 = (hw >> 6) & 0x1F
        rn, rd = (hw >> 3) & 7, hw & 7
        nm = "ldrh" if l else "strh"
        i.text = "%s %s, [%s, #%d]" % (nm, R[rd], R[rn], imm5 << 1)
        i.mem, i.base, i.imm, i.kind = True, rn, imm5 << 1, nm
        return i
    if b15_12 == 0b1001:
        # fmt 11 SP-relative
        l = (hw >> 11) & 1
        rd = (hw >> 8) & 7
        imm = (hw & 0xFF) << 2
        nm = "ldr" if l else "str"
        i.text = "%s %s, [sp, #%d]" % (nm, R[rd], imm)
        i.mem, i.base, i.imm, i.kind = True, -2, imm, nm
        return i
    if b15_12 == 0b1010:
        sp = (hw >> 11) & 1
        rd = (hw >> 8) & 7
        imm = (hw & 0xFF) << 2
        i.text = "add %s, %s, #%d" % (R[rd], "sp" if sp else "pc", imm)
        return i
    if b15_12 == 0b1011:
        if ((hw >> 10) & 3) == 0:
            sub = (hw >> 7) & 1
            imm = (hw & 0x7F) << 2
            i.text = "%s sp, #%d" % ("sub" if sub else "add", imm)
            return i
        if ((hw >> 9) & 3) == 2:
            l, r = (hw >> 11) & 1, (hw >> 8) & 1
            lst = hw & 0xFF
            regs = [R[k] for k in range(8) if lst & (1 << k)]
            if r:
                regs.append("pc" if l else "lr")
            i.text = "%s {%s}" % ("pop" if l else "push", ", ".join(regs))
            if l:
                i.kind = "pop"
            return i
        if ((hw >> 9) & 3) == 0:
            l = (hw >> 11) & 1
            rn = (hw >> 8) & 7
            lst = hw & 0xFF
            regs = [R[k] for k in range(8) if lst & (1 << k)]
            i.text = "%s %s!, {%s}" % ("ldmia" if l else "stmia", R[rn], ", ".join(regs))
            i.mem, i.base, i.kind = True, rn, "ldmia" if l else "stmia"
            return i
        return i
    if b15_12 == 0b1100:
        l = (hw >> 11) & 1
        rn = (hw >> 8) & 7
        lst = hw & 0xFF
        regs = [R[k] for k in range(8) if lst & (1 << k)]
        i.text = "%s %s!, {%s}" % ("ldmia" if l else "stmia", R[rn], ", ".join(regs))
        i.mem, i.base, i.kind = True, rn, "ldmia" if l else "stmia"
        return i
    if b15_12 == 0b1101:
        cond = (hw >> 8) & 0xF
        if cond == 0xF:
            i.text = "svc #%d" % (hw & 0xFF)
            return i
        if cond == 0xE:
            i.text = "udf #%d" % (hw & 0xFF)
            return i
        off = sx(hw & 0xFF, 8) << 1
        cn = ["beq", "bne", "bcs", "bcc", "bmi", "bpl", "bvs", "bvc",
              "bhi", "bls", "bge", "blt", "bgt", "ble", "udf", "svc"][cond]
        i.text = "%s 0x%06x" % (cn, (addr + 4 + off) & 0xFFFFFFFF)
        i.kind = "cond"
        return i
    if b15_11 == 0b11100:
        off = sx(hw & 0x7FF, 11) << 1
        i.text = "b 0x%06x" % ((addr + 4 + off) & 0xFFFFFFFF)
        i.kind = "b"
        return i
    if b15_11 == 0b11110:
        hi = sx(hw & 0x7FF, 11) << 12
        i.text = "bl.hi 0x%x" % (hi & 0xFFFFFFFF)
        i.kind = "blhi"
        return i
    if b15_11 == 0b11111:
        lo = (hw & 0x7FF) << 1
        i.text = "bl.lo"
        i.kind = "bllo"
        return i
    return i


def load(path):
    with open(path, "rb") as f:
        return f.read()


def dis_window(data, lo, hi, out=sys.stdout):
    a = lo
    while a < hi:
        if a + 2 > len(data):
            break
        hw = struct.unpack_from("<H", data, a)[0]
        ins = decode(hw, a)
        print("0x%06X  %04X  %s" % (a, hw, ins.text), file=out)
        a += 2


def find_pool_refs(data, lo, hi):
    """Yield (addr, reg, literal_addr, value) for every LDR-literal."""
    a = lo
    while a < hi:
        hw = struct.unpack_from("<H", data, a)[0]
        ins = decode(hw, a)
        if ins.lit is not None and 0 <= ins.lit <= len(data) - 4:
            val = struct.unpack_from("<I", data, ins.lit)[0]
            yield (a, ins, val)
        a += 2


def main():
    data = load(sys.argv[1])
    cmd = sys.argv[2] if len(sys.argv) > 2 else "dis"
    if cmd == "dis":
        dis_window(data, int(sys.argv[3], 0), int(sys.argv[4], 0))
    elif cmd == "gpio":
        for (a, ins, val) in find_pool_refs(data, 0, len(data)):
            if val in GPIO_BASES:
                print("0x%06X  ldr r?  <- 0x%08X  (%s)" % (a, val, GPIO_BASES[val]))
    elif cmd == "sweep":
        out = open(sys.argv[3], "w")
        for (a, ins, val) in find_pool_refs(data, 0, len(data)):
            if 0x40000000 <= val < 0x40080000 or 0x1FFF0000 <= val <= 0x1FFFFFFF:
                print("0x%06X  pool=0x%08X  val=0x%08X" % (a, ins.lit, val), file=out)
        out.close()


if __name__ == "__main__":
    main()
