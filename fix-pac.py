import glob
import os

# svd2rust emits a leading run of inner attributes (#![...]) which are illegal
# inside an include!()d file. Strip them; lib.rs carries the equivalents.
for p in glob.glob('pac-crate/src/pac.rs'):
    d = open(p, encoding='utf-8').read()
    marker = '#[doc = r"Number available'
    j = d.find(marker)
    if j < 0:
        # fall back: find the end of the first line (attrs are newline-separated here)
        j = d.find('\n') + 1
    head = d[:j]
    open(p, 'w', encoding='utf-8').write(d[j:])
    print('stripped %d bytes of inner attributes from %s' % (j, p))
    print('first 200 stripped chars:', head[:200])
print('new pac.rs size:', os.path.getsize('pac-crate/src/pac.rs'))
