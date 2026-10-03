#!/usr/bin/env python3
"""
funcdump.py - disassemble a function from a raw Cortex-M0 image, resolving
literal pools and printing pool words separately.

  python funcdump.py IMAGE.bin 0x11470 [maxinsns]
"""
import struct
import sys

import thumbdis as T


def main():
    data = open(sys.argv[1], "rb").read()
    pc = int(sys.argv[2], 0)
    maxi = int(sys.argv[3]) if len(sys.argv) > 3 else 400
    pool_targets = []
    n = 0
    while n < maxi and pc + 1 < len(data):
        hw = struct.unpack_from("<H", data, pc)[0]
        ins = T.decode(hw, pc)
        line = "0x%06X  %04X  %s" % (pc, hw, ins.text)
        if ins.lit is not None:
            v = struct.unpack_from("<I", data, ins.lit)[0] if ins.lit + 4 <= len(data) else None
            line += "        ; pool@0x%06X = 0x%08X" % (ins.lit, v)
            pool_targets.append(ins.lit)
        print(line)
        n += 1
        pc += 2
        if ins.kind in ("bx",) and ins.text.startswith("bx lr"):
            # function end: dump the following pool
            print("  --- literal pool after 0x%06X ---" % (pc - 2))
            p = (pc + 3) & ~3
            for _ in range(16):
                if p + 4 > len(data):
                    break
                v = struct.unpack_from("<I", data, p)[0]
                print("    0x%06X = 0x%08X" % (p, v))
                p += 4
                if p - ((pc + 3) & ~3) > 64:
                    break
            break


if __name__ == "__main__":
    main()
