import sys, struct
p = sys.argv[1]
out = sys.argv[2]
d = open(p,'rb').read()
VT = 0x1BAA4C           # where the real vector table starts
BASE = 0                 # handlers say the image is linked at 0

# Determine image extent: find the largest handler/jump address to estimate size,
# then round up to a sensible flash page multiple.
maxptr = 0
for k in range(256):
    v = struct.unpack_from('<I', d, VT + k*4)[0]
    if 0x100 <= v < 0x100000 and (v & 1):
        maxptr = max(maxptr, v & ~1)
print("highest handler/jump pointer: 0x%X (%d)" % (maxptr, maxptr))

size = 0x10000
img = d[VT:VT+size]
open(out,'wb').write(img)
print("extracted %d bytes -> %s" % (len(img), out))

nz = sum(1 for b in img if b != 0)
print("non-zero: %d (%.1f%%)" % (nz, 100.0*nz/len(img)))

# where does it stop looking like code? find last non-0xFF, non-0x00 run
tail = img.rstrip(b'\x00').rstrip(b'\xff')
print("significant length (trimming 00/FF tail): %d bytes (0x%X)" % (len(tail), len(tail)))
if len(img) > len(tail):
    print("trailing padding from 0x%X to 0x%X" % (len(tail), len(img)))

# printable strings in the image - tells us what the firmware DOES
print()
print("=== printable strings (>=6 chars) in the firmware image ===")
cur=[]; start=0
found=[]
for i,b in enumerate(img):
    if 32 <= b < 127:
        if not cur: start=i
        cur.append(chr(b))
    else:
        if len(cur) >= 6:
            found.append((start, "".join(cur)))
        cur=[]
if len(cur)>=6: found.append((start,"".join(cur)))
for off,s in found[:60]:
    print("  0x%05X  %s" % (off, s))
print("  total strings:", len(found))
