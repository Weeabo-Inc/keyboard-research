import sys, struct
p = sys.argv[1]
d = open(p,'rb').read()
print("file size:", len(d))

# --- PE parse: find end of all sections to spot an overlay ---
if d[:2] != b'MZ':
    print("not MZ"); sys.exit()
pe_off = struct.unpack_from('<I', d, 0x3C)[0]
assert d[pe_off:pe_off+4] == b'PE\0\0', "no PE sig"
nsec = struct.unpack_from('<H', d, pe_off+6)[0]
opt_size = struct.unpack_from('<H', d, pe_off+20)[0]
magic = struct.unpack_from('<H', d, pe_off+24)[0]
print("PE sections:", nsec, "opt magic: 0x%X" % magic, "(%s)" % ("PE32+" if magic==0x20b else "PE32"))
sec_off = pe_off + 24 + opt_size
end = 0
print("\nsections:")
for i in range(nsec):
    o = sec_off + i*40
    name = d[o:o+8].rstrip(b'\0').decode('latin1')
    vsize = struct.unpack_from('<I', d, o+8)[0]
    rawsize = struct.unpack_from('<I', d, o+16)[0]
    rawptr = struct.unpack_from('<I', d, o+20)[0]
    print("  %-8s vsize=%-8d rawsize=%-8d rawptr=%-8d" % (name, vsize, rawsize, rawptr))
    end = max(end, rawptr+rawsize)
print("\nend of last section:", end)
print("overlay size:", len(d)-end)
if len(d) > end:
    ov = d[end:]
    print("overlay first 32 bytes:", ov[:32].hex())
    # entropy of overlay
    from collections import Counter
    c = Counter(ov[:262144])
    print("overlay distinct bytes (first 256KB):", len(c), "/256")
    # find where entropy drops (likely the raw firmware region)
    print("\nscanning for ARM Cortex-M vector table patterns (SP in SRAM, reset in flash):")
    hits = 0
    for off in range(0, len(ov)-8, 4):
        sp, rv = struct.unpack_from('<II', ov, off)
        if 0x20000000 <= sp <= 0x20010000 and 0x00000000 <= rv < 0x00020000 and (rv & 1):
            print("  overlay+0x%X  SP=0x%08X  reset=0x%08X" % (off, sp, rv))
            hits += 1
            if hits >= 12: break
    if not hits:
        print("  none found in overlay")
