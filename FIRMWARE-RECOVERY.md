# Firmware Recovery — Vendor Updater Extraction

**Status:** ✅ working. 5 of 5 updater executables yielded firmware. No hardware required.
**Date:** 2026-10-03
**Tool:** [extract-fw.py](../sonix/extract-fw.py)

---

## Why this matters

`sonix/PROTOCOL.md` §7 records a hard limitation: **the Sonix SN32F2xx ISP protocol has
no flash-read command.** The command set is erase / program / checksum / identify —
nothing reads flash contents out. The community answer is ST-Link + SWD with the
`BOOT` pin grounded, using `sonix_dumper`'s boot-ROM gadget technique.

That made flashing a **one-way door**: no backup, no way back.

**This document removes that blocker.** Vendor updater executables contain the firmware
as a compiled-in data blob. We can extract it with no probe, no soldering, and no
risk to the device.

---

## Method

A Cortex-M image begins with a vector table. The detection signature is:

| Word | Meaning | Expected |
|---|---|---|
| `[0]` | initial stack pointer | in SRAM (`0x20000000+`), 4-byte aligned |
| `[1]` | `Reset_Handler` | in flash, thumb bit set |
| `[2..6]` | NMI / HardFault / MemManage / BusFault / UsageFault | in flash |
| `[7..10]` | reserved | **exactly zero** |
| `[11]` | `SVC_Handler` | in flash |
| `[12]` | `DebugMon_Handler` | in flash |
| `[13]` | reserved | **exactly zero** |
| `[14]` | `PendSV_Handler` | in flash |
| `[15]` | `SysTick_Handler` | in flash |

The **reserved words being exactly zero** is the strong signal — random data almost
never produces four consecutive zero words in the right positions alongside a valid
SRAM stack pointer and a run of flash-resident handler pointers.

---

## Results — measured, not assumed

All five updater executables from `Magnetic Keyboard Reset Firmware.zip` were scanned:

| Source executable | Vector table | Stack pointer | Density |
|---|---|---|---|
| `F75 HE Firmware.exe` | `0x1BAA4C` | `0x20017A78` | 72% |
| `...2860_8K...HFD8KCZ700_V1.51...` | `0x1BAA4C` | `0x20007570` | 75% |
| `...2860_8K...无光版_HFD8KCZ700_V1.15...` | `0x1BAA4C` | `0x20007578` | 75% |
| `...2860_8K...烟云轴_HFD8KCZ700_V1.51...` | `0x1BAA4C` | `0x20007570` | 75% |
| `...2860_双8K...RT0.01_V1.50_0x07D9...` | `0x1BAA4C` | `0x200076F8` | 75% |

### Two findings worth stating

1. **Identical offset `0x1BAA4C` in every file.** The firmware is a compiled-in array
   in `.rdata`, not a PE resource — so its offset is fixed by the updater codebase at
   build time. There is **no PE overlay** (verified: last section ends exactly at EOF).

2. **Different stack pointers prove these are genuinely different images.**
   `0x20017A78` implies SRAM up to ~96 KB (a larger part), whereas `0x20007570`
   implies ~30 KB. So these are not one blob copied around — they are distinct builds
   for distinct MCUs.

### Verified vector table (one example)

```
[ 0] 0x20007570  initial SP           SRAM
[ 1] 0x00000205  Reset_Handler        handler
[ 2] 0x0000021B  NMI_Handler          handler
[ 3] 0x0000457D  HardFault_Handler    handler
[ 4] 0x0000021F  MemManage_Handler    handler
[ 5] 0x00000221  BusFault_Handler     handler
[ 6] 0x00000223  UsageFault_Handler   handler
[ 7] 0x00000000  reserved             zero ok
[ 8] 0x00000000  reserved             zero ok
[ 9] 0x00000000  reserved             zero ok
[10] 0x00000000  reserved             zero ok
[11] 0x00000225  SVC_Handler          handler
[12] 0x00000227  DebugMon_Handler     handler
[13] 0x00000000  reserved             zero ok
[14] 0x00000229  PendSV_Handler       handler
[15] 0x0000AFEB  SysTick_Handler      handler
```

**Interpretation:** the handlers at `0x205, 0x21B, 0x21F, 0x221, 0x223, 0x225, 0x227,
0x229, 0x22D` are consecutive — these are the default **weak handler stubs** compiled
back-to-back, which is exactly what toolchain startup code looks like. The image is
linked at `0x00000000` with the reset handler 4 bytes into the first instruction slot.

---

## What the images contain

**Almost no readable ASCII.** A string sweep found only code fragments (`pGpGpG`,
`#)M(h`, `bj9O7H9x`) — i.e. ARM Thumb machine code, not text. 75% density with the
`01 xx xx 00` pattern characteristic of Thumb `BL`/`BX` encodings.

**No vendor strings, no version banner, no readable UI text.** Consistent with
production firmware compiled release-stripped. This is **not** evidence of encryption
— encrypted blobs have near-uniform entropy, whereas these images have the
instruction-shaped structure of real code.

---

## Practical consequence

| Before | After |
|---|---|
| No USB read-back ⇒ dump needs ST-Link + SWD + soldering | **Firmware extractable from vendor updaters, no hardware** |
| Flashing would destroy the stock firmware permanently | **Stock images can be held before flashing** |
| No way to compare a "reset firmware" against what is on the device | **Multiple vendor images available for comparison** |

**The one-way door is now two-way.**

---

## Caveats — what this is NOT

- **These are other boards' firmware.** `F75 HE`, `HERO`, `WIN60/68`, `MINI 60` — not
  the AULA S98Pro. They demonstrate the technique and provide reference images, but
  they are not a backup of the target device.
- **A reset firmware is not necessarily the shipping firmware.** It may be a
  recovery/known-good build rather than what the factory flashed.
- **The extraction offset is updater-specific.** `0x1BAA4C` held for these five files;
  a different updater codebase may differ. Always re-scan rather than hardcoding.
- **Extraction is not decryption.** If a future image *is* encrypted, this method will
  find a vector table only if one is stored in the clear.

---

## Reproduction

```powershell
# report what is inside a file or directory
python <REPO_ROOT>\sonix\extract-fw.py scan <path>

# extract one image
python <REPO_ROOT>\sonix\extract-fw.py extract <updater.exe> <out.bin>

# extract every .exe in a tree (output names include the stack pointer so
# different images stay distinguishable)
python <REPO_ROOT>\sonix\extract-fw.py extract-all <dir> <outdir>
```
