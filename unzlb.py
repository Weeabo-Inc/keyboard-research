import zlib, os, sys
src = r'<REPO_ROOT>\keyboard\s98-installer\[0]'
d = open(src,'rb').read()
print("input: %d bytes, header %s" % (len(d), d[:4].hex()))
# Inno uses 'zlb' + 0x1a then a zlib stream starting at offset 4
for start in (4, 0):
    try:
        out = zlib.decompress(d[start:])
        print("*** decompressed from offset %d: %d bytes ***" % (start, len(out)))
        dst = r'<REPO_ROOT>\keyboard\s98-installer\payload_unpacked.bin'
        open(dst,'wb').write(out)
        print("wrote", dst)
        print("first 64 bytes:", out[:64].hex())
        print("ascii:", ''.join(chr(b) if 32<=b<127 else '.' for b in out[:64]))
        # find embedded filenames
        import re
        names = re.findall(rb'[\x20-\x7e]{4,}\.(?:exe|dll|ini|xml|dat|bin|hex|bmp|gif|png|ico)', out)
        seen=[]
        for n in names:
            s=n.decode('latin1')
            if s not in seen: seen.append(s)
        print("\nembedded file names (%d unique):" % len(seen))
        for s in seen[:40]: print("   ", s)
        break
    except Exception as e:
        print("offset %d failed: %s" % (start, e))
