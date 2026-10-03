import sys, struct
from collections import Counter
import math
p = sys.argv[1]
d = open(p,'rb').read()
print("size: %d (0x%X)" % (len(d), len(d)))
sp, rv = struct.unpack_from('<II', d, 0)
print()
print("=== vector table at offset 0 ===")
names = {0:'initial SP',1:'Reset_Handler',2:'NMI_Handler',3:'HardFault_Handler',
         4:'MemManage',5:'BusFault',6:'UsageFault',7:'reserved',8:'reserved',
         9:'reserved',10:'reserved',11:'SVC_Handler',12:'DebugMon',13:'reserved',
         14:'PendSV',15:'SysTick'}
for k in range(16):
    v = struct.unpack_from('<I', d, k*4)[0]
    print("  [%2d] 0x%08X  %-18s" % (k, v, names.get(k,'')))
print()
print("=== is the image 4-byte aligned overall? SP aligned: %s ===" % (sp % 4 == 0))
print("reserved[7..10] all zero:", all(struct.unpack_from('<I',d,k*4)[0]==0 for k in (7,8,9,10)))
print("reserved[13] zero:", struct.unpack_from('<I',d,13*4)[0]==0)

# thumb instruction sanity: sample code region, count 16-bit BL/BX-like patterns
code = d[0x400:0x2000]
print()
print("code sample @0x400, first 48 bytes:", code[:48].hex())

# find approximate end of code: last offset where 0xFF density is low
BLK=4096
print()
print("=== block map (4KB blocks) - F=mostly padding, C=code/data ===")
row=""
for i in range(0, len(d), BLK):
    blk = d[i:i+BLK]
    ff = blk.count(0xFF)
    frac = ff/len(blk)
    row += "F" if frac > 0.9 else ("f" if frac > 0.5 else "C")
print("  ", row)
print("   (each char = 4KB, 64 chars = 256KB)")
