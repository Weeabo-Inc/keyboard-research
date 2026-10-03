<div align="center">
	<img width=140 src="assets/cover.svg" />
	<h2>keyboard-research</h2>
</div>

[![License](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)]()
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-0078D6.svg?style=flat-square)]()
[![Language](https://img.shields.io/badge/language-Python-3776AB.svg?style=flat-square)]()
[![Status](https://img.shields.io/badge/status-research-purple.svg?style=flat-square)]()

### Reverse engineering a keyboard that did not want to be opened.

USB and HID enumeration, report-descriptor capture, vendor protocol probing, and PE-resource firmware extraction. Built against a Sonix-based mechanical keyboard, but the techniques apply to any HID device and any Cortex-M firmware shipped inside a vendor installer.

---

### The subject

```
Wired  : 0C45:800A   manufacturer "SONiX"   product "AULA-S98Pro"
Dongle : 05AC:024F   product "S98Pro Dongle"   <- spoofs Apple's vendor ID
MCU    : SN32F290 (256 KB flash, Cortex-M0)
```

**The dongle spoofing Apple's VID is not malicious.** It is how cheap boards get macOS to auto-bind HID without prompting for a driver. But it is worth knowing, and it is why the device first appeared to be an *Apple Magic Keyboard* to anything that trusted vendor IDs.

---

### The interface map

| Interface | usage page | in | out | feature | role |
|---|---|---|---|---|---|
| MI_00 | `0x0001` | 9 | 2 | 0 | boot keyboard plus LED |
| MI_01 | `0x000C`, `0x0001` | 3, 2, 16, 5, 4 | 0 | 0 | media, system, NKRO, mouse |
| MI_02 | `0xFF68` | 65 | **4097** | 0 | bulk and LCD image channel |
| **MI_03** | **`0xFF13`** | **65** | **65** | **65** | **command channel** |

**`MI_03` is the only interface that accepts feature reports.** Established by trying all four and reading the errors:

```
MI_00: HidD_SetFeature failed (win32 Incorrect function)
MI_01: HidD_SetFeature failed (win32 Incorrect function)
MI_02: HidD_SetFeature failed (win32 Incorrect function)
MI_03: accepted
```

> **Do not send large `SET_REPORT` control transfers to `MI_02`.** Documented to hard-crash that firmware. LCD data must go through interrupt OUT on EP3, 4096 bytes. That 4097 byte output report exists for exactly one reason, pushing large binary blobs, and the wrong framing kills it.

---

### The tools

#### Device and interface enumeration

| Tool | Purpose |
|---|---|
| [`hid-lld.py`](hid-lld.py) | **Low-level probe.** `HidP_GetCaps`, link-collection tree, raw report descriptors, no driver change. |
| [`hid-capture.py`](hid-capture.py) | HID report descriptors and device strings through `hidapi` |
| [`via-probe.py`](via-probe.py) | Read-only VIA/QMK `GET_PROTOCOL` handshake |

`hid-lld.py` is the useful one. It opens each collection and reports usage page, usage, report lengths and the link-collection tree. Read-only, nothing written to the device.

```
[8&3b95e2d1&0&0000]
  usage page     : 0xFF60   usage 0x0061
  report lengths : in=33 out=33 feature=0
```

`0xFF60` with usage `0x61` and a 33 byte bidirectional report is **QMK's raw HID interface, verbatim**: `RAW_USAGE_PAGE 0xFF60`, `RAW_USAGE_ID 0x61`, `RAW_EPSIZE 32`, plus a report ID byte.

#### PE and firmware analysis

| Tool | Purpose |
|---|---|
| [`pe_scan.py`](pe_scan.py) | PE section layout and overlay detection |
| [`pe_rsrc.py`](pe_rsrc.py) | Walk the resource directory, report every blob with RVA and size |
| [`find_vectors.py`](find_vectors.py) | Locate ARM Cortex-M vector tables |
| [`extract_fw.py`](extract_fw.py) | Carve the firmware image out |
| [`find_magic.py`](find_magic.py) | Hunt protocol constants in a binary |

---

### The firmware extraction story

**The premise this broke:** Sonix's ISP protocol has no flash-read command. Firmware was believed to require ST-Link plus SWD with the `BOOT` pin grounded. And flashing without a backup destroys the stock firmware permanently.

**It requires none of that.** Vendor updaters carry the firmware inside themselves.

The route for one real tool:

```
S98PRO Firemware.exe
└── PE resource type 0xA
    ├── blob @ RVA 0x1C564C, 262144 bytes   <- a 256 KB firmware image
    └── blob @ RVA 0x20567C, 145091 bytes   <- starts "PK\x03\x04", it is a ZIP
        └── Settings/FWFiles/SN32F290.hex   <- THE FIRMWARE, as Intel HEX
            Settings/UISettings.ini         <- the flasher's config, solid gold
```

**The vector-table scanner correctly reported "no vector table found" on the outer executable, and that negative result was the clue** to look at resources instead.

Validated: **5 of 5** updaters from one vendor pack yielded firmware, all with the vector table at the identical offset `0x1BAA4C`, because the firmware is a compiled-in array in `.rdata` rather than a PE resource.

**And the stack pointers differed** (`0x20017A78` against `0x20007570`), proving these are genuinely different images for different parts, not one blob copied around.

---

### What the vendor's own config gave us

Extracting `Settings\UISettings.ini` from Sonix's flasher was worth more than any amount of byte-grepping the binary:

```ini
ISPTitle=HFD ISP Tool
CheckDeviceCmd=AA55A55AFF0033CC      ; the ISP entry command
CheckBootLoaderID=1
CurVersion=0x0109
[SpecialPID0] VID=0x0C45 PID=0x8009 ChipName=SN32F290 FWName=SN32F290.hex CodeSecurity=1
```

**Two independent confirmations from that one file:**

1. **The MCU is `SN32F290`**, named outright rather than inferred from a model number
2. **`CheckDeviceCmd=AA55A55AFF0033CC`** read little-endian is `0x5AA555AA` plus `0xCC3300FF`, which is **byte for byte the reboot magic** independently derived from SonixFlasherC's source

**Read the vendor's own configuration before you disassemble anything.** It told us what hours of inference had been circling.

---

### A tool that silently fails to launch

Sonix's ISP tool **exits immediately** if its `Settings\` folder is missing. No error, no dialog, nothing. It needs `UISettings.ini`, `FWFiles\SN32F290.hex` and its UI bitmaps.

Decompiling the loader later showed why: it reads the ini from `%TEMP%\SettingFiles\Settings\UISettings.ini`, not from its own directory. No config, no `CheckDeviceCmd`, no title, no firmware filename, so it quits.

Reconstructing the folder made it start:

```
Process : S98PRO_Firmware
Title   : HFD ISP Tool     <- read from ISPTitle in the ini, proof it loaded the config
```

---

### What it can and can't do

**Can do:**
- Enumerate HID down to report-descriptor and link-collection level
- Identify the command interface by measurement rather than assumption
- Probe vendor protocols read-only
- Walk PE resources and carve embedded firmware
- Validate extracted firmware against Cortex-M vector table structure
- Find protocol constants in a stripped binary

**Can't do:**
- Force the device into ISP bootloader mode. This is the unsolved problem.

Nine attempts, all measured, all failed:

| Attempt | Result |
|---|---|
| vendor `CheckDeviceCmd` verbatim | echoed back, recognised but not obeyed |
| user-guide variant | all zeros, not recognised |
| both reboot magics | no transition |
| single-byte commands `0x20` to `0xFF` | all return zero status |
| `FN+ESC` plus replug, live USB watch | vanished at 25 s, **returned as `0x800A` in under 150 ms** |
| any ISP PID | **never appeared** |

That is recorded because a negative result is a result. The remaining routes are the keyboard's own screen menus, which are unexplored, and the physical `BOOT` pin.

**Also can't do:** dump flash over USB. No such command exists in the ISP protocol. Firmware comes from installers or from a hardware programmer.

---

### Project layout

```
keyboard-research/
├── assets/
│   └── cover.svg
├── hid-lld.py            low-level HID probe
├── hid-capture.py        report descriptors via hidapi
├── via-probe.py          VIA/QMK handshake, read-only
├── pe_scan.py            PE sections and overlays
├── pe_rsrc.py            resource directory walker
├── find_vectors.py       ARM vector table locator
├── extract_fw.py         firmware carver
├── find_magic.py         protocol constant hunter
├── FIRMWARE-RECOVERY.md  how the firmware was recovered
└── GHIDRA-HANDOFF.md     what to look for in the vendor tool
```

---

### Credits

- [Wireshark](https://www.wireshark.org/) and the USB capture tooling that made the protocol visible
- [SonixQMK](https://github.com/SonixQMK) for the bootloader documentation
- [Twemoji](https://github.com/twitter/twemoji) for the cover art, CC BY 4.0
- [Ghidra](https://ghidra-sre.org/), which turned a 2.2 MB binary into something readable

---

<div align="center">
	<br/>
	<i>a negative result is a result. nine of them are in here.</i>
	<br/>
	<sub>all probing tools are read-only by default.</sub>
</div>
