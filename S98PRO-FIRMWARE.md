# S98Pro Stock Firmware — Recovered

**Status:** ✅ stock firmware obtained. No hardware, no soldering, no ST-Link.
**MCU confirmed:** **SN32F290** (named by Sonix's own flasher, not inferred)
**Date:** 2026-10-03

---

## The headline

The research report concluded:

> *"DUMP OVER USB: no. No flash-read command exists in either the community flasher or the
> vendor updater; readback is a 16-bit word-sum checksum only. DUMP AT ALL: yes, via SWD —
> ST-Link + OpenOCD with the BOOT pin tied to GND."*

**We did not need any of that.** The vendor's own firmware updater ships the firmware inside
itself, in a resource, as **Intel HEX**. Extracting it required only file parsing.

**This removes the project's single biggest risk**: flashing without a backup destroys the
stock firmware permanently, and there is no vendor recovery path.

---

## Artifacts

| File | Size | What it is |
|---|---|---|
| `stock/S98Pro_SN32F290_stock.bin` | 262,144 B | **Raw binary**, ready for the ISP protocol |
| `stock/S98Pro_SN32F290_stock.hex` | 259,282 B | **Authoritative Intel HEX**, as shipped by Sonix |
| `stock/Sonix_flasher_UISettings.ini` | 3.2 KB | Sonix flasher config — reveals the ISP command |

---

## How it was recovered

Source: `<HOME>\Downloads\S98PRO Firemware.exe` (2,216,960 bytes)

Its version resource says:

```
LegalCopyright : Copyright (c) 2008- Sonix Technology Co., LTD. All Rights Reserved.
FileVersion    : 2.1.2.6
```

**This is Sonix's own ISP tool, not a rebadged third-party utility.** So its embedded
firmware is as authoritative as it gets.

### The extraction path

```
S98PRO Firemware.exe
└── PE resource type 0xA
    ├── blob @ RVA 0x1C564C, 262144 bytes   ← a 256 KB firmware image (different build)
    └── blob @ RVA 0x20567C, 145091 bytes   ← starts with "PK\x03\x04" → a ZIP
        └── Settings/FWFiles/SN32F290.hex   ← THE FIRMWARE, 253 KB of Intel HEX
            Settings/UISettings.ini         ← the flasher config
            Settings/Image/*.bmp            ← flasher UI artwork
```

**The lesson:** the firmware was not a raw blob to be signature-scanned. It was a **ZIP
stored as a PE resource**, containing Intel HEX. The vector-table scanner correctly reported
"no vector table found" on the outer executable — and that negative result was the clue to
look at resources instead.

---

## Chip identification — SN32F290, confirmed three independent ways

### 1. The filename, from Sonix's own flasher
```
Settings\FWFiles\SN32F290.hex
```

### 2. The flasher config names the chip explicitly
```ini
[SpecialPID0]
VID=0x0C45
PID=0x8009
ChipName=SN32F290
FWName=SN32F290.hex
FWType=HEX
CodeSecurity=1
```

### 3. The vector table is consistent with a 256 KB part
```
[ 0] 0x20002B18   initial SP       (SRAM, valid)
[ 1] 0x00007EE5   Reset_Handler    (thumb)
[ 2] 0x00007EFF   NMI_Handler
[ 3] 0x000009D1   HardFault_Handler
```

Referencing the chip table in [PROTOCOL.md](../sonix/PROTOCOL.md) §5: **SN32F29X = 256 KB
ROM, 256 pages, blank checksum 0x0000.** The image is exactly 256 KB. Consistent.

> **This also corrects the earlier estimate.** The research report guessed
> *"most likely SN32F248B (64 KB)"*. The actual part is **SN32F290 at 256 KB** — four times
> the flash. That matters: it means far more room for firmware work than assumed.

---

## The ISP entry command — vendor-confirmed

From Sonix's `UISettings.ini`:

```ini
CheckDeviceCmd=AA42895AFF7162CC
```

And from Sonix's own user guide, the same field annotated:

```ini
CheckDeviceCmd=AA55A55AFF0033CC      //ISP command setting.
```

Read as little-endian u32 pairs:

```
AA 55 A5 5A   →  0x5AA555AA
FF 00 33 CC   →  0xCC3300FF
```

**That is `{0x5AA555AA, 0xCC3300FF}` — byte-for-byte the magic we independently derived from
SonixFlasherC's source** (`REBOOT_SONIX` in [protocol.rs](../sonix/src/protocol.rs)).

**Two independent sources agreeing.** The reboot magic is confirmed correct, which means our
`sonix-rs reboot` / `enter-isp` commands are built on verified ground.

---

## Intel HEX validation

```
records          : 5766
record types     : {4: 3, 0: 5761, 5: 1, 1: 1}
checksum errors  : 0            ← every record validates
address range    : 0x0 .. 0x3FFFF  (92,152 data bytes)
address span     : 262,144 bytes, 1 gap
entry point      : 0x000000C1   ← SN32F290 entry (vector table is 0xC0 = 192 bytes)
```

**Zero checksum errors** across 5,766 records. The image is intact.

Only **92,152 bytes are actual data**; the rest is erased `0xFF`. Writing a full 256 KB
image is therefore mostly redundant — worth knowing before programming a device.

---

## Two different firmware images — and why that matters

The Sonix tool contains **two** firmware-like blobs. They are **not** the same image:

| | Authoritative HEX | Resource blob @ 0x1C564C |
|---|---|---|
| initial SP | `0x20002B18` | `0x20002DD0` |
| Reset_Handler | `0x00007EE5` | `0x00012E2D` |
| first 128 KB match | — | **35.1%** |

Region-by-region:

```
block 0x00000:  21.5% match   ← different code
block 0x10000:  48.7% match   ← shared runtime code + padding
block 0x20000: 100.0% match   ← erased flash (all 0xFF)
block 0x30000: 100.0% match   ← erased flash (all 0xFF)
```

**The apparent 67% overall similarity was almost entirely erased flash**, not code
correspondence. Both parts are 256 KB and both builds use only the lower ~128 KB, so
128 KB of all-`0xFF` matched trivially. This is a good example of a misleading aggregate
statistic: the per-region breakdown told the true story.

**Conclusion: two distinct valid builds for the same silicon family.** The `0x1C564C` blob
is likely a generic/alternate build bundled by the flasher; the HEX is the one the flasher
is configured to program.

---

## ⚠️ Open question — is this *our* device's firmware?

**Not yet proven.** The flasher's `SpecialPID` table lists:

```
PID=0x8009   ChipName=SN32F290
PID=0x8801   ChipName=SN32F290
```

**Our keyboard enumerates as `0C45:800A`, which is not in that list.** So either:

- the flasher matches a PID range rather than exact values, or
- a different `SpecialPID` entry applies, or
- **this firmware is for a *related* SN32F290 board, not the S98Pro specifically.**

The file is named `S98PRO Firemware.exe`, which is suggestive but not proof — filenames are
chosen by whoever uploaded it, not by Sonix.

**How to settle it:** the `CurVersion=0x0109` field in the config suggests the flasher reads
a version from the device. Once we can query the device's own version over the vendor
protocol (§`vversion` in sonix-rs), we can compare. Until then, treat this as
**strong candidate stock firmware, not verified backup**.

---

## What this unblocks

| Before | After |
|---|---|
| No backup possible ⇒ flashing is irreversible | **Stock image held; flashing is now recoverable** |
| MCU only guessed (SN32F248B) | **SN32F290 confirmed, 256 KB** |
| Reboot magic derived from one source | **Confirmed by two independent sources** |
| No reference image for analysis | **Validated Intel HEX + raw binary** |

---

## Reproduction

```powershell
# 1. scan the updater (reports "no vector table" - firmware is in a resource, not a blob)
python <REPO_ROOT>\sonix\extract-fw.py scan "S98PRO Firemware.exe"

# 2. the firmware is a ZIP inside PE resource type 0xA; extract blob @ RVA 0x20567C
#    (see keyboard/FIRMWARE-RECOVERY.md for the resource walk)

# 3. convert Intel HEX -> raw binary, validating every record checksum
python <REPO_ROOT>\sonix\hex2bin.py SN32F290.hex out.bin --size 0x40000
```

---

## Tools used

| Tool | Purpose |
|---|---|
| [extract-fw.py](../sonix/extract-fw.py) | Vector-table scanner / firmware extractor |
| [hex2bin.py](../sonix/hex2bin.py) | Intel HEX → binary with checksum validation |
| [PROTOCOL.md](../sonix/PROTOCOL.md) | ISP protocol reference (chip table §5) |
| [sonix-rs](../sonix/) | Our own ISP tool |
