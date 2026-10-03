# keyboard-research

**Reverse-engineering tooling for a Sonix-based mechanical keyboard** — USB/HID
enumeration, report-descriptor capture, vendor protocol probing, and PE-resource
firmware extraction.

Built against an **AULA S98Pro** (tri-mode mechanical keyboard, LCD, RGB, magnetic-switch
sibling models) but the techniques apply to any HID device and any Cortex-M firmware
shipped inside a vendor updater.

---

## The device

```
Wired  : 0C45:800A   manufacturer "SONiX"   product "AULA-S98Pro"
Dongle : 05AC:024F   product "S98Pro Dongle"   ← spoofs Apple's vendor ID
MCU    : SN32F290 (256 KB flash, Cortex-M0)
```

**The dongle spoofing Apple's VID is not malicious** — it's how cheap boards get macOS to
auto-bind HID without prompting for a driver. But it's worth knowing, and it's why the
device first appeared to be an *Apple Magic Keyboard* to VID-based tooling.

---

## The interface map

| Interface | usage page | in | out | feature | role |
|---|---|---|---|---|---|
| MI_00 | `0x0001` | 9 | 2 | 0 | boot keyboard + LED |
| MI_01 | `0x000C`, `0x0001` | 3, 2, 16, 5, 4 | 0 | 0 | media / system / NKRO / mouse |
| MI_02 | `0xFF68` | 65 | **4097** | 0 | bulk / LCD image channel |
| **MI_03** | **`0xFF13`** | **65** | **65** | **65** | **command channel** |

**`MI_03` is the only interface that accepts feature reports** — established by trying all
four and reading the errors, not by assuming:

```
MI_00: HidD_SetFeature failed (win32 Incorrect function)
MI_01: HidD_SetFeature failed (win32 Incorrect function)
MI_02: HidD_SetFeature failed (win32 Incorrect function)
MI_03: ✅ accepted
```

> ### ⚠️ Do not send large `SET_REPORT` control transfers to `MI_02`.
>
> Documented to hard-crash that firmware. LCD data must go via interrupt OUT on EP3,
> 4096 bytes. `MI_02`'s 4097-byte output report exists for exactly one reason — pushing
> large binary blobs — and the wrong framing kills it.

---

## Tools

### Device & interface enumeration

| Tool | Purpose |
|---|---|
| [`hid-capture.py`](hid-capture.py) | HID report descriptors and device strings via `hidapi` |
| [`hid-lld.py`](hid-lld.py) | **Low-level probe** — `HidP_GetCaps`, link-collection tree, raw report descriptors with no driver change |
| [`via-probe.py`](via-probe.py) | Read-only VIA/QMK `GET_PROTOCOL` handshake |

`hid-lld.py` is the useful one. It opens each collection and reports usage page, usage,
report lengths and the link-collection tree — **read-only, nothing written to the device**:

```
[8&3b95e2d1&0&0000]
  usage page     : 0xFF60   usage 0x0061
  report lengths : in=33 out=33 feature=0
```

`0xFF60` with usage `0x61` and a 33-byte bidirectional report is **QMK's raw HID
interface, verbatim** — that's `RAW_USAGE_PAGE 0xFF60`, `RAW_USAGE_ID 0x61`, `RAW_EPSIZE 32`
plus a report-ID byte.

### PE / firmware analysis

| Tool | Purpose |
|---|---|
| [`pe_scan.py`](pe_scan.py) | PE section layout and overlay detection |
| [`pe_rsrc.py`](pe_rsrc.py) | Walk the resource directory, report every blob with RVA and size |
| [`find_vectors.py`](find_vectors.py) | Locate ARM Cortex-M vector tables |
| [`extract_fw.py`](extract_fw.py) | Carve the firmware image out |

---

## The firmware extraction story

**The premise this broke:** Sonix's ISP protocol has *no flash-read command*. Firmware was
believed to require ST-Link + SWD with the `BOOT` pin grounded. And flashing without a backup
destroys the stock firmware permanently.

**It doesn't require any of that.** Vendor updaters carry the firmware inside themselves.

The route for one real tool:

```
S98PRO Firemware.exe
└── PE resource type 0xA
    ├── blob @ RVA 0x1C564C, 262144 bytes   ← a 256 KB firmware image
    └── blob @ RVA 0x20567C, 145091 bytes   ← starts "PK\x03\x04" → a ZIP
        └── Settings/FWFiles/SN32F290.hex   ← THE FIRMWARE, as Intel HEX
            Settings/UISettings.ini         ← the flasher's config (gold)
```

**The vector-table scanner correctly reported "no vector table found" on the outer
executable — and that negative result was the clue** to look at resources instead.

Validated: **5 of 5** updaters from one vendor pack yielded firmware, all with the vector
table at the identical offset `0x1BAA4C` — because the firmware is a compiled-in array in
`.rdata`, not a PE resource, so its offset is fixed by the build.

**And the stack pointers differed** (`0x20017A78` vs `0x20007570`), proving these are
genuinely different images for different parts, not one blob copied around.

---

## What the vendor's own config gave us

Extracting `Settings\UISettings.ini` from Sonix's flasher was worth more than any amount of
byte-grepping the binary:

```ini
ISPTitle=HFD ISP Tool
CheckDeviceCmd=AA55A55AFF0033CC      ; the ISP entry command
CheckBootLoaderID=1
CurVersion=0x0109
[SpecialPID0] VID=0x0C45 PID=0x8009 ChipName=SN32F290 FWName=SN32F290.hex CodeSecurity=1
```

**Two independent confirmations from that one file:**

1. **The MCU is `SN32F290`** — named outright, not inferred from a model number.
2. **`CheckDeviceCmd=AA55A55AFF0033CC`** read little-endian is
   `0x5AA555AA` + `0xCC3300FF` — **byte-for-byte the reboot magic** independently derived
   from [SonixFlasherC](https://github.com/SonixQMK/SonixFlasherC)'s source.

**Read the vendor's own configuration before you disassemble anything.** It told us what
hours of inference had been circling.

---

## A real tool that silently fails to launch

Sonix's ISP tool **exits immediately** if its `Settings\` folder is missing — no error, no
dialog, nothing. It needs `UISettings.ini`, `FWFiles\SN32F290.hex` and its UI bitmaps.

Reconstructing the folder from the resource ZIP made it start:

```
Process : S98PRO_Firmware
Title   : HFD ISP Tool     ← read from ISPTitle in the ini — proof it loaded the config
```

`FIRMWARE-RECOVERY.md` and `GHIDRA-HANDOFF.md` document the rest.

---

## Scope, honestly

**Demonstrated:** HID enumeration down to report-descriptor and link-collection level; the
command channel identified by measurement; vendor protocol probing; PE resource walking;
firmware extraction from updaters (6 files, 5 of 5 in one batch); Intel HEX validation
(5,766 records, 0 checksum errors).

**Not achieved:** forcing the device into ISP bootloader mode. The app firmware
**acknowledges every command and never hands off**:

| Attempt | Result |
|---|---|
| ini `CheckDeviceCmd` verbatim | echoed back — recognised, not obeyed |
| user-guide variant | all zeros — not recognised |
| both reboot magics | no transition |
| single-byte commands `0x20`–`0xFF` | all return zero-status |
| `FN+ESC` + replug (live USB watch) | vanished at 25 s, **returned as `0x800A` in <150 ms** |
| any ISP PID | **never appeared** |

That's recorded because a negative result is a result. The remaining routes are the
keyboard's own screen menus (**unexplored**) and the physical `BOOT` pin.

---

## Ethics

Everything here targets **hardware owned by the author**. All probing tools are read-only
by default; the destructive operations (erase, program) were deliberately left unimplemented
because the platform has no flash read-back, making a flash irreversible without a prior
image.

## License

MIT
