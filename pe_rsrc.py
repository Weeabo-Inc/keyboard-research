import sys, struct, os
from collections import Counter
p = sys.argv[1]
d = open(p,'rb').read()
print("file size: %d (0x%X)" % (len(d), len(d)))

pe_off = struct.unpack_from('<I', d, 0x3C)[0]
assert d[pe_off:pe_off+4] == b'PE\0\0'
nsec = struct.unpack_from('<H', d, pe_off+6)[0]
opt_size = struct.unpack_from('<H', d, pe_off+20)[0]
magic = struct.unpack_from('<H', d, pe_off+24)[0]
print("PE32%s, sections=%d" % ("+" if magic==0x20b else "", nsec))
sec_off = pe_off + 24 + opt_size
end = 0
for i in range(nsec):
    o = sec_off + i*40
    name = d[o:o+8].rstrip(b'\0').decode('latin1')
    vsize = struct.unpack_from('<I', d, o+8)[0]
    rawsize = struct.unpack_from('<I', d, o+16)[0]
    rawptr = struct.unpack_from('<I', d, o+20)[0]
    print("  %-8s vsize=%-10d rawsize=%-10d rawptr=%-10d" % (name, vsize, rawsize, rawptr))
    end = max(end, rawptr+rawsize)
print("end of sections: %d   overlay: %d bytes" % (end, len(d)-end))

# resource directory: walk it properly via .rsrc
# find .rsrc
rsrc = None
for i in range(nsec):
    o = sec_off + i*40
    if d[o:o+8].rstrip(b'\0') == b'.rsrc':
        rsrc = (struct.unpack_from('<I', d, o+20)[0], struct.unpack_from('<I', d, o+16)[0])
print()
if rsrc:
    base, size = rsrc
    print("=== .rsrc at 0x%X size %d - enumerating resource TYPES ===" % (base, size))
    # IMAGE_RESOURCE_DIRECTORY at base
    nname = struct.unpack_from('<H', d, base+12)[0]
    nid   = struct.unpack_from('<H', d, base+14)[0]
    print("named entries: %d, id entries: %d" % (nname, nid))
    off = base + 16
    for k in range(nname+nid):
        e = off + k*8
        nameid = struct.unpack_from('<I', d, e)[0]
        dataoff = struct.unpack_from('<I', d, e+4)[0]
        isdir = dataoff & 0x80000000
        nm = dataoff & 0x7FFFFFFF
        print("   entry %d: id/name=0x%08X dir=%s offset=0x%X" % (k, nameid, bool(isdir), nm))
        # walk one level for size info
        if isdir:
            sub = base + nm
            sn = struct.unpack_from('<H', d, sub+12)[0]
            si = struct.unpack_from('<H', d, sub+14)[0]
            for j in range(sn+si):
                se = sub + 16 + j*8
                snameid = struct.unpack_from('<I', d, se)[0]
                sdataoff = struct.unpack_from('<I', d, se+4)[0]
                if sdataoff & 0x80000000:
                    sub2 = base + (sdataoff & 0x7FFFFFFF)
                    sn2 = struct.unpack_from('<H', d, sub2+12)[0]
                    si2 = struct.unpack_from('<H', d, sub2+14)[0]
                    for m in range(sn2+si2):
                        me = sub2 + 16 + m*8
                        mnameid = struct.unpack_from('<I', d, me)[0]
                        mdo = struct.unpack_from('<I', d, me+4)[0]
                        if not (mdo & 0x80000000):
                            de = base + mdo
                            rva, sz = struct.unpack_from('<II', d, de)
                            print("        -> lang 0x%X: data RVA=0x%X size=%d (0x%X)" % (mnameid, rva, sz, sz))
