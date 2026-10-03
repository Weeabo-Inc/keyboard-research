#!/usr/bin/env python3
"""
Const-propagating Thumb-1 scanner: resolves GPIO register accesses in a raw
Cortex-M0 image.

Tracks register values that are known constants (from LDR-literal, MOVS/MOV
immediate, ADDS) and reports every load/store whose effective address lands in
a GPIO block. Conservative: any unrecognised instruction invalidates the
destination register only.

READ-ONLY.
  python gpioscan.py IMAGE.bin all
  python gpioscan.py IMAGE.bin fn 0x6000 0x7000
"""
import struct
import sys

R = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
     "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]

GPIO = {0x40044000: "GPIO0", 0x40046000: "GPIO1",
        0x40048000: "GPIO2", 0x4004A000: "GPIO3"}

REG = {0x00: "DATA", 0x04: "MODE", 0x08: "CFG", 0x0C: "IS",
       0x10: "IEV", 0x14: "IE", 0x18: "RIS", 0x1C: "MIS",
       0x20: "IC", 0x24: "SET", 0x28: "CLR", 0x30: "CFG1"}


def sx(v, b):
    m = 1 << (b - 1)
    return (v ^ m) - m


class Dec:
    """Returns (kind, info). kind: 'lit','imm','mem','alu','term','b','other','bl'"""

    def __init__(self, data):
        self.d = data

    def w(self, a):
        return struct.unpack_from("<H", self.d, a)[0]

    def word(self, a):
        if 0 <= a <= len(self.d) - 4:
            return struct.unpack_from("<I", self.d, a)[0]
        return None

    def decode(self, a):
        hw = self.w(a)
        h15_13 = hw >> 13
        h15_12 = hw >> 12
        h15_11 = hw >> 11
        h15_10 = hw >> 10
        h15_9 = hw >> 9

        # fmt1 shift imm  (000 op) : bits15:11 in 00000/00001/00010
        if h15_13 == 0b000 and ((hw >> 11) & 3) != 3:
            sub = (hw >> 11) & 3
            imm = (hw >> 6) & 0x1F
            rd, rs = hw & 7, (hw >> 3) & 7
            return ("alu", ("lsls" if sub == 0 else "lsrs" if sub == 1 else "asrs",
                            rd, rs, imm))
        # fmt2 add/sub reg or imm3
        if h15_11 == 0b00011:
            immf = (hw >> 10) & 1
            sub = (hw >> 9) & 1
            rn, rs, rd = (hw >> 6) & 7, (hw >> 3) & 7, hw & 7
            if rn in (13, 15):      # ADR / ADD Rd,PC|SP
                return ("adr", (rd, R[rn], immf))
            if not immf and not sub:
                return ("add_reg", (rd, rs, rn))
            return ("addsub", ("subs" if sub else "adds", rd, rs, rn, immf))
        # fmt3 mov/cmp/add/sub imm8
        if h15_13 == 0b001:
            sub = (hw >> 11) & 3
            rd, imm = (hw >> 8) & 7, hw & 0xFF
            if sub == 0:
                return ("movimm", (rd, imm))
            if sub == 2:
                return ("addimm", (rd, imm))
            return ("alu", ("cmp/add/sub", rd, imm))
        # fmt4 data processing reg
        if h15_10 == 0b010000:
            sub = (hw >> 6) & 0xF
            rs, rd = (hw >> 3) & 7, hw & 7
            return ("alu", (sub, rd, rs))
        # fmt5 special / bx
        if h15_10 == 0b010001:
            sub = (hw >> 8) & 3
            rs = (((hw >> 6) & 1) << 3) | ((hw >> 3) & 7)
            rd = (((hw >> 7) & 1) << 3) | (hw & 7)
            if sub == 2 and rs == 15:      # mov rd, pc
                return ("adr", (rd, "pc", 0))
            if sub == 3:
                return ("bx", (rs,))
            if sub == 2:
                return ("movreg", (rd, rs))
            return ("other", (sub, rd, rs))
        # fmt6 ldr literal
        if h15_11 == 0b01001:
            rd = (hw >> 8) & 7
            imm = (hw & 0xFF) << 2
            return ("lit", (rd, ((a + 4) & ~3) + imm))
        # fmt 7/9 bits15:12 == 0101
        if h15_12 == 0b0101:
            if ((hw >> 9) & 7) != 0:
                sub = (hw >> 9) & 7
                rm, rn, rd = (hw >> 6) & 7, (hw >> 3) & 7, hw & 7
                return ("memreg", (sub, rd, rn, rm))
            l = (hw >> 11) & 1
            imm5 = (hw >> 6) & 0x1F
            rn, rd = (hw >> 3) & 7, hw & 7
            return ("mem", ("ldr" if l else "str", rd, rn, imm5 << 2, "w"))
        # fmt 10 halfword imm  bits15:12 == 1000
        if h15_12 == 0b1000:
            l = (hw >> 11) & 1
            imm5 = (hw >> 6) & 0x1F
            rn, rd = (hw >> 3) & 7, hw & 7
            return ("mem", ("ldrh" if l else "strh", rd, rn, imm5 << 1, "h"))
        # fmt 8 byte imm  bits15:12 == 0111
        if h15_12 == 0b0111:
            l = (hw >> 11) & 1
            imm5 = (hw >> 6) & 0x1F
            rn, rd = (hw >> 3) & 7, hw & 7
            return ("mem", ("ldrb" if l else "strb", rd, rn, imm5, "b"))
        if h15_12 == 0b0110:
            l = (hw >> 11) & 1
            imm5 = (hw >> 6) & 0x1F
            rn, rd = (hw >> 3) & 7, hw & 7
            return ("mem", ("ldrh" if l else "strh", rd, rn, imm5 << 1, "h"))
        # fmt11 sp-relative
        if h15_12 == 0b1001:
            l = (hw >> 11) & 1
            rd = (hw >> 8) & 7
            imm = (hw & 0xFF) << 2
            return ("mem", ("ldr" if l else "str", rd, -2, imm, "w"))
        if h15_12 == 0b1010:
            sp = (hw >> 11) & 1
            rd = (hw >> 8) & 7
            return ("adr", (rd, "sp" if sp else "pc", (hw & 0xFF) << 2))
        if h15_12 == 0b1011:
            if ((hw >> 10) & 3) == 0:
                return ("other", ("addsp",))
            if ((hw >> 9) & 3) == 2:
                l = (hw >> 11) & 1
                return ("pop" if l else "other", (hw & 0xFF,))
            if ((hw >> 9) & 3) == 0:
                l = (hw >> 11) & 1
                rn = (hw >> 8) & 7
                return ("stmia", (l, rn, hw & 0xFF))
            return ("other", ("fmt11",))
        if h15_12 == 0b1100:
            l = (hw >> 11) & 1
            rn = (hw >> 8) & 7
            return ("ldmia" if l else "stmia", (rn, hw & 0xFF))
        if h15_12 == 0b1101:
            cond = (hw >> 8) & 0xF
            if cond == 0xF:
                return ("svc", (hw & 0xFF,))
            off = sx(hw & 0xFF, 8) << 1
            return ("cond", (cond, a + 4 + off))
        if h15_11 == 0b11100:
            return ("b", (a + 4 + (sx(hw & 0x7FF, 11) << 1),))
        if h15_11 == 0b11110:
            return ("blhi", (sx(hw & 0x7FF, 11) << 12,))
        if h15_11 == 0b11111:
            return ("bllo", ((hw & 0x7FF) << 1,))
        return ("other", ("unk",))


def sweep(data, lo, hi, out):
    dec = Dec(data)
    # const map: reg -> value (None = unknown). r13 sp, r15 pc special.
    cv = [None] * 16
    cv[13] = "SP"
    cv[15] = "PC"
    a = lo
    in_pool = False
    while a < hi - 1:
        kind, info = dec.decode(a)
        # ---- literal pool detection: after an unconditional terminator, the
        #      next word(s) up to the next 4-aligned address are data.
        if in_pool:
            if a % 4 == 0 and False:
                pass
        if kind == "lit":
            rd, tgt = info
            v = dec.word(tgt)
            cv[rd] = v
            a += 2
            continue
        if kind == "movimm":
            rd, imm = info
            cv[rd] = imm
            a += 2
            continue
        if kind == "addimm":
            rd, imm = info
            cv[rd] = (cv[rd] + imm) if isinstance(cv[rd], int) else None
            a += 2
            continue
        if kind == "add_reg":
            rd, rs, rn = info
            if isinstance(cv[rs], int) and isinstance(cv[rn], int):
                cv[rd] = cv[rs] + cv[rn]
            else:
                cv[rd] = None
            a += 2
            continue
        if kind == "addsub":
            _, rd, rs, rn, immf = info
            base = cv[rs] if isinstance(cv[rs], int) else None
            val = rn if immf else (cv[rn] if isinstance(cv[rn], int) else None)
            if base is not None and isinstance(val, int):
                cv[rd] = (base - val) if info[0] == "subs" else (base + val)
            else:
                cv[rd] = None
            a += 2
            continue
        if kind == "adr":
            rd, r, imm = info
            if r == "pc":
                cv[rd] = ((a + 4) & ~3) + imm
            else:
                cv[rd] = None
            a += 2
            continue
        if kind == "movreg":
            rd, rs = info
            cv[rd] = cv[rs] if rd < 8 else None
            a += 2
            continue
        if kind == "mem":
            nm, rd, rn, imm, sz = info
            base = rn if rn >= 0 else cv[13]
            bv = cv[rn] if rn >= 0 else cv[13]
            addr = (bv + imm) if isinstance(bv, int) else None
            note = ""
            if addr is not None:
                for gb, gn in GPIO.items():
                    if gb <= addr < gb + 0x40:
                        note = "   <<<< %s_%s" % (gn, REG.get(addr - gb, "+0x%X" % (addr - gb)))
            if addr is not None and note:
                extra = ""
                if nm.startswith("str") and isinstance(cv[rd], int):
                    extra = "  value=0x%X" % cv[rd]
                elif nm.startswith("str") and cv[rd] is not None and not isinstance(cv[rd], int):
                    extra = "  value=%s" % cv[rd]
                print("0x%06X  %-10s %s, [%s + 0x%X] = 0x%08X%s%s"
                      % (a, nm, R[rd], R[rn] if rn >= 0 else "sp", imm, addr, extra, note), file=out)
            if nm.startswith("ldr"):
                cv[rd] = None
            a += 2
            continue
        if kind == "ldmia":
            rn, lst = info
            cv[rn] = None
            for k in range(8):
                if lst & (1 << k):
                    cv[k] = None
            a += 2
            continue
        if kind == "stmia":
            a += 2
            continue
        if kind == "pop":
            lst = info[0]
            for k in range(8):
                if lst & (1 << k):
                    cv[k] = None
            a += 2
            continue
        if kind == "alu":
            t = info[0]
            if isinstance(t, int):
                cv[info[1]] = None
            elif t in ("lsls", "lsrs", "asrs") and isinstance(cv[info[2]], int) and isinstance(info[3], int):
                sh = info[3] or 32
                v = cv[info[2]]
                cv[info[1]] = (v << sh) & 0xFFFFFFFF if t == "lsls" else (v >> sh)
            else:
                cv[info[1]] = None
            a += 2
            continue
        if kind == "bx":
            a += 2
            continue
        if kind == "b":
            a += 2
            continue
        if kind == "cond":
            a += 2
            continue
        # unknown: invalidate nothing but advance
        a += 2


def main():
    data = open(sys.argv[1], "rb").read()
    mode = sys.argv[2]
    out = sys.stdout
    if mode == "all":
        sweep(data, 0, len(data) - 2, out)
    elif mode == "fn":
        sweep(data, int(sys.argv[3], 0), int(sys.argv[4], 0), out)


if __name__ == "__main__":
    main()
