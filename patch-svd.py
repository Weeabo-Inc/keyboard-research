import re
import xml.etree.ElementTree as ET

src = 'SN32F290.svd'
dst = 'SN32F290.patched.svd'

d = open(src, encoding='utf-8').read()

# 1. CMSIS requires cpu mpuPresent/fpuPresent; Cortex-M0 has neither.
anchor = '<vendorSystickConfig>false</vendorSystickConfig>'
assert anchor in d
d = d.replace(anchor, anchor + '\n    <mpuPresent>false</mpuPresent>\n    <fpuPresent>false</fpuPresent>', 1)

# 2. Strip empty <enumeratedValues> blocks (schema-invalid).
d, n_empty = re.subn(r'[ \t]*<enumeratedValues>\s*</enumeratedValues>\n', '', d)

open('_s1.svd', 'w', encoding='utf-8').write(d)

IDENT_OK = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def sanitise(name):
    s = re.sub(r'[^A-Za-z0-9_]+', '_', name).strip('_')
    if not s or not re.match(r'[A-Za-z_]', s):
        s = 'V_' + s
    return s


root = ET.parse('_s1.svd').getroot()

# Walk the real structure so renames are scoped: an enumeratedValue's own name
# only has to be unique inside its <field>/<enumeratedValues> container, and must
# not collide with the field name or a register name (svd2rust derives a reader/
# writer method name from each).
edits = []          # (old, new)

# svd2rust generates inherent methods on its writer/reader wrappers. An
# enumeratedValue whose sanitised name collides with one of those (e.g. an enum
# variant literally called "New" -> `New::new()`) breaks code generation, so
# these names must never be used as enum variant names.
RESERVED = {'new', 'variant', 'bits', 'set', 'clear', 'is_set', 'is_clear',
            'modify', 'write', 'read', 'reset', 'width', 'offset', 'mask'}

# Every enumeratedValue name must be unique across the whole device: svd2rust
# hoists each field's enum into its own module, but shares one `FieldWriter`
# impl per field, so cross-scope collisions still bite.
seen = set()


def fix_container(container, extra):
    evs = container.find('enumeratedValues')
    if evs is None:
        return
    for ev in evs.findall('enumeratedValue'):
        nel = ev.find('name')
        if nel is None or not nel.text:
            continue
        old = nel.text
        new = sanitise(old)
        if new.lower() in RESERVED:
            new = new + '_V'
        base = new
        k = 2
        while new in seen or new in extra:
            new = '%s_%d' % (base, k)
            k += 1
        seen.add(new)
        if new != old:
            edits.append((old, new))


for per in root.iter('peripheral'):
    pname = per.findtext('name')
    for reg in per.iter('register'):
        rname = reg.findtext('name')
        fix_container(reg, {pname, rname})
        for fld in reg.iter('field'):
            fname = fld.findtext('name')
            fix_container(fld, {pname, rname, fname})

# Apply only within <enumeratedValue> name tags, to avoid touching a field or
# register that happens to share the text.
n_applied = 0
for old, new in sorted(set(edits), key=lambda kv: -len(kv[0])):
    pat = re.compile(r'(<enumeratedValue>\s*<name>)%s(</name>)' % re.escape(old))
    d, k = pat.subn(lambda m: m.group(1) + new + m.group(2), d)
    n_applied += k

open(dst, 'w', encoding='utf-8').write(d)
print('removed %d empty enumeratedValues block(s)' % n_empty)
print('scoped enumeratedValue renames: %d distinct, %d applied' % (len(set(edits)), n_applied))

left = [n for n in re.findall(r'<name>([^<]*)</name>', d) if not IDENT_OK.match(n)]
print('non-identifier <name> remaining: %d' % len(left))
for x in sorted(set(left))[:10]:
    print('   ', repr(x))
