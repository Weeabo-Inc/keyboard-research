# SN32F290 Rust Firmware Feasibility

**Scope:** what it takes to write custom Rust firmware for the SN32F290 in the AULA S98Pro.
**Status of this document:** research only. Nothing was flashed, no tool was run against hardware, and
`<REPO_ROOT>\sonix\` was not modified. All local analysis was read-only against the stock image at
`<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin`.

**Evidence tags used below**

| Tag | Meaning |
| --- | --- |
| `[V]` | Verified from a primary source (vendor SVD/datasheet, or read directly out of the stock image) |
| `[V-C]` | Verified from community source code (SonixQMK / ChibiOS-Contrib) |
| `[I]` | Inference — follows from verified facts, not directly observed |
| `[?]` | Unknown — could not be determined; stated as unknown rather than guessed |

---

## Summary

**Verdict: feasible with caveats.**

The hard part is not the port. It is the recovery path.

Positives, and they are better than expected:

- The part is a **Cortex-M0 (ARMv6-M, `thumbv6m-none-eabi`)** — `[V]` vendor SVD says `CM0 r0p0`, and the
  stock image's exception vector table at `0x00000000` is a textbook ARM layout.
- **The Rust PAC is not hypothetical — I generated and compiled it during this research.** Sonix's own
  **CMSIS-SVD for the SN32F290** exists (35 peripherals, 422 registers, 6011 enumerated values, and zero
  `derivedFrom`/`dim`/`cluster`). `svd2rust` 0.37.1 turns it into a **5.0 MB `lib.rs`** that **compiles
  clean for `thumbv6m-none-eabi`**, and a smoke test confirms the peripheral API we need — including the
  `IVTM` register that the recovery primitive writes. Three genuine defects in the vendor SVD had to be
  patched; all three are documented and scripted in §3.2. `[V]`
- **A complete C reference port already exists** for this exact part: SonixQMK's ChibiOS-Contrib has
  `SN32F290.ld`, an `SN32F290` HAL directory, a vendor `SN32F290.h`, and QMK itself has an
  `SN32F299F` MCU target with a working `sn32f290` keyboard. The register map is not something we have to
  reverse-engineer. `[V-C]`
- **The ISP bootloader is in a separate masked boot ROM at `0x1FFF0000`**, *not* in user flash, so a
  botched firmware write cannot erase it. `[V]`/`[I]`

The caveats, in descending order of importance:

1. **Entry into ISP mode is performed by the application firmware, not by the ROM.** We proved this from
   the stock image: the firmware contains a USB handler that compares exactly our magic against
   `0x5A8942AA` / `0xCC6271FF` and then branches straight to the ROM entry at `0x1FFF0301`. Replace that
   firmware and *the vendor's entry trigger is gone*. Our custom firmware must implement its own.
2. **The hardware fallback (`BOOT` pin) is unverified for this part and unknown for this board.** The
   community documents `BOOT`-to-GND for SN32F248B/SN32F260 and it is the reason those ports are safe to
   experiment on. **I could not find the SN32F290 `BOOT` pin assignment, and I could not find it on the
   S98Pro PCB.** Until that is established, we have no *proven* independent recovery path.
3. **The SN32F290's USB device controller has no Rust driver.** It is a full-speed USB device controller,
   but `usb-device` gives you the class layer only; the `UsbBus` implementation must be written. That is
   the bulk of the real work.
4. Cortex-M0 has **no atomic read-modify-write**, which constrains which `no_std` crates can be used
   unmodified.

So: the engineering is well-supported by existing art, and the toolchain exists. The risk is entirely
concentrated in "if the custom firmware is broken, what gets us back in?" — and today the honest answer is
"I cannot yet prove anything except 'the app jumps to ROM'". Section 5 gives the procedure that fixes
that ordering problem.

---

## 1. Hardware facts

### 1.1 Core

| Property | Value | Source |
| --- | --- | --- |
| Core | ARM Cortex-M0, revision r0p0 | `[V]` SVD `<cpu><name>CM0</name><revision>r0p0</revision>` |
| Architecture | ARMv6-M, Thumb-1 only | `[V]` same |
| Rust target | `thumbv6m-none-eabi` | `[V]` standard triple |
| NVIC priority bits | **2** | `[V]` SVD `<nvicPrioBits>2</nvicPrioBits>` |
| SysTick | vendor-configured, not ARM-standard | `[V]` SVD `<vendorSystickConfig>false</vendorSystickConfig>` |
| CPU clock | up to **48 MHz** | `[V]` SVD device description: *"ARM 32-bit Cortex-M0 Microcontroller based device, CPU clock up to 48MHz"* |
| Endianness | little | `[V]` SVD |
| Address unit | 8-bit, register width 32-bit | `[V]` SVD |

**The 8051-vs-ARM question is settled: ARM, definitively.** Three independent confirmations:

1. The vendor SVD declares `CM0`. `[V]`
2. The stock image begins with `18 2B 00 20 E5 7E 00 00` → initial SP `0x20002B18`, reset vector
   `0x00007EE5`. A valid Cortex-M vector table: SP inside SRAM, reset vector odd (Thumb bit) and inside
   flash. `[V]` (read from the image)
3. The exception vectors are in the correct ARM order — vector 11 = `0x00007F03` (SVCall), vector 14 =
   `0x00007F05` (PendSV), vector 15 = `0x00006F51` (SysTick). `[V]` (read from the image)

Sonix does make 8051-class parts (`SN8F`/`SN8P`, e.g. `SN8P2267CF` = eVision `VS11K18A`), and there is a
Rust flasher crate for that family (`sn8flash`). **None of that applies here.** The SN32F line is ARM.

### 1.2 Memory map

| Region | Base | Size | Notes |
| --- | --- | --- | --- |
| User flash | `0x00000000` | **256 KB** (to `0x0003FFFF`) | `[V]` ChibiOS `SN32F290.ld`; matches the 262144-byte stock image |
| SRAM | `0x20000000` | **32 KB** (to `0x20007FFF`) | `[V]` ChibiOS `SN32F290.ld` |
| Boot ROM (ISP) | `0x1FFF0000` | ~4 KB | `[V-C]`; entry point at `0x1FFF0301` |
| UC registers (inside ROM region) | `0x1FFF2450` | — | `[V]` SVD peripheral `SN_UC` |
| Code-security / option word | `0x3FFFC` | 4 bytes | `[V]` ChibiOS `_flag_start = 0x3FFFC`; present in the stock image |

**32 KB SRAM is confirmed independently by the stock image itself**: its initial stack pointer is
`0x20002B18`, which needs more than 8 KB and fits comfortably in 32 KB. `[V]`

Flash-page geometry: the ISP identifies the SN32F29x as **256 pages of 1 KB** (`ROM_PAGES_F290 256`,
`ROM_SIZE_F290 256`). `[V]` `SonixFlasherC/include/chip.h`. The **host-side** erase granularity is
therefore 1 KB. The FMC's own (`SN_FLASH.CTRL`: `PG` / `PER` page-erase / `MER` mass-erase) granularity is
`[?]` — I did not verify whether an on-die page equals the 1 KB ISP page.

### 1.3 The `.flag` / code-security word

The stock image's final flash word is **`0xAAAA5555`** (bytes at `0x3FFFC`: `55 55 AA AA`). `[V]` (read
from the image)

`SonixFlasherC` recognises security values `0x0000`/`0xFFFF` = CS0, `0x5A5A` = CS1, `0xA5A5` = CS2,
`0x55AA` = CS3. `[V]` **`0xAAAA5555` is not in that table**, so either it encodes a different scheme or
the two 16-bit halves are read in the other order (`0x5555`, `0xAAAA`) — also not in the table. `[I]`
Either way this is the **code option / security** word, and it sits in the last 1 KB erase page. Our
flasher should read it (ISP `0x29 Get code option`) and preserve or deliberately set it rather than
letting a full-image write clobber it blindly. **Flagging this as a real hazard:** writing the last page
destroys this word.

### 1.4 Peripherals — full register map

From the vendor SVD. Every peripheral is a 0x2000-byte aperture.

| Peripheral | Base | IRQ | Regs |
| --- | --- | --- | --- |
| `SN_CT16B0` | `0x40000000` | 15 | 22 |
| `SN_CT16B1` | `0x40002000` | 16 | 29 |
| `SN_CT16B2` | `0x40004000` | 17 | 17 |
| `SN_CT16B3` | `0x40006000` | 19 | 18 |
| `SN_CT16B4` | `0x40008000` | 20 | 18 |
| `SN_CT16B5` | `0x4000A000` | 21 | 17 |
| `SN_WDT` | `0x40010000` | 25 | 3 |
| `SN_RTC` | `0x40012000` | 23 | 7 |
| `SN_I2S1` | `0x40014000` | 4 | 8 |
| `SN_UART0` | `0x40016000` | 13 | 14 |
| `SN_I2C0` | `0x40018000` | 10 | 11 |
| `SN_I2S0` | `0x4001A000` | 3 | 8 |
| `SN_SPI0` | `0x4001C000` | 6 | 13 |
| `SN_ADC` | `0x40026000` | 24 | 5 |
| `SN_CMP` | `0x40028000` | 5 | 11 |
| `SN_OPA` | `0x4002A000` | — | 1 |
| `SN_PMU` | `0x40032000` | — | 1 |
| `SN_LCD` | `0x40034000` | 2 | 15 |
| `SN_EBI` | `0x40036000` | 22 | 26 |
| `SN_CRC` | `0x40038000` | — | 2 |
| `SN_PFPA` | `0x40042000` | — | 10 |
| `SN_GPIO0` | `0x40044000` | 31 | 12 |
| `SN_GPIO1` | `0x40046000` | 30 | 12 |
| `SN_GPIO2` | `0x40048000` | 29 | 11 |
| `SN_GPIO3` | `0x4004A000` | 28 | 12 |
| `SN_UART3` | `0x40052000` | 9 | 14 |
| `SN_UART2` | `0x40054000` | 8 | 14 |
| `SN_UART1` | `0x40056000` | 14 | 14 |
| `SN_SPI1` | `0x40058000` | 7 | 9 |
| `SN_I2C1` | `0x4005A000` | 11 | 11 |
| **`SN_USB`** | **`0x4005C000`** | **1** | **30** |
| `SN_SYS1` | `0x4005E000` | — | 4 |
| `SN_SYS0` | `0x40060000` | 0 | 13 |
| `SN_FLASH` | `0x40062000` | — | 8 |
| `SN_UC` | `0x1FFF2450` | — | 2 |

**This table is not a guess about our chip — it is cross-validated against our own firmware.** I scanned
the stock image for every one of these base addresses as a 32-bit literal. Every single one that the
application plausibly uses appears verbatim: `SN_USB` ×5, `SN_GPIO0` ×17, `SN_GPIO1` ×13, `SN_GPIO2` ×5,
`SN_GPIO3` ×8, `SN_SYS0` ×6, `SN_SYS1` ×6, `SN_FLASH` ×4, all six `CT16Bx`, both SPI, `SN_LCD` ×1,
`SN_WDT` ×7, `SN_PFPA` ×1. `[V]`

Notably the stock firmware **drives the on-die `SN_LCD` peripheral** (`0x40034000` referenced once, plus
an `SN_SYS1.AHBCLKEN` LCD clock-enable bit) — so the S98Pro's display is at least partly driven from the
MCU, not only from the external SPI NOR. `[I]` (from a single literal reference and a clock-enable bit)

### 1.5 USB controller

**Yes — it is a full-speed USB 2.0 device controller**, on-die. SVD description for `SN_USB`:
*"Universal Serial Bus Full Speed Device Interface (USB)"*. `[V]`

Base `0x4005C000`, IRQ 1, 30 registers. Key ones:

| Offset | Name | Purpose |
| --- | --- | --- |
| `0x00` | `INTEN` | Interrupt enable |
| `0x04` | `INSTS` | Interrupt event status |
| `0x08` | `INSTSC` | Interrupt event status clear |
| `0x0C` | `ADDR` | Device address |
| `0x10` | `CFG` | Configuration |
| `0x14` | `SGCTL` | Signal control |
| `0x18` | `EP0CTL` | Endpoint 0 control |
| `0x1C`, `0x20`, `0x24`, `0x28`, `0x2C`, `0x30` | `EP1CTL`…`EP6CTL` | Endpoint 1–6 control |
| `0x3C` | `EPTOGGLE` | Data toggle |
| `0x48`, `0x4C`, `0x50`, `0x54`, `0x58`, `0x5C` | `EP1BUFOS`…`EP6BUFOS` | Endpoint buffer offsets |
| `0x60` | `FRMNO` | Frame number |
| `0x64`, `0x6C` | `PHYPRM`, `PHYPRM2` | PHY parameters |
| `0x70` | `PS2CTL` | PS/2 control (the part has a PS/2 mode) |
| `0x78`/`0x7C`/`0x80`, `0x84`/`0x88`/`0x8C` | `RWADDR`/`RWDATA`/`RWSTATUS` (+ `2`) | Buffer RAM access |

**7 endpoints (EP0–EP6)** and a separate buffer-RAM window accessed via `RWADDR`/`RWDATA`. That is a
plentiful endpoint budget for a keyboard (our device already uses 4 interfaces). `[V]`

### 1.6 System control — the two registers that matter for a port

**`SN_SYS0` @ `0x40060000`**

| Offset | Register | Notes |
| --- | --- | --- |
| `0x04` | `PLLCTRL` | `MSEL[3:0]`, `PSEL[5]`, `PLLCLKSEL[12]`, `PLLEN[15]` |
| `0x08` | `CSST` | `IHRCRDY`, `ELSRDY`, `EHSRDY`, `PLLRDY` |
| `0x0C` | `CLKCFG` | `SYSCLKSEL[3:0]`, `SYSCLKST[7:4]` |
| `0x10` | `AHBCP` | `AHBPRE[3:0]`, `DIV1P5[5:4]` |
| `0x14` | `RSTST` | `SWRSTF`, `WDTRSTF`, `LVDRSTF`, `EXTRSTF`, `PORRSTF` |
| `0x20` | `SWDCTRL` | `SWDDIS[0]` |
| `0x24` | `IVTM` | `IVTM[2:0]`, `IVTMKEY[31:16]` — **interrupt vector table mapping** |

**`SN_SYS1` @ `0x4005E000`**: `AHBCLKEN` (0x00), `APBCP0` (0x04), `APBCP1` (0x08), `PRST` (0x10).
`AHBCLKEN` bit assignments are known from the ChibiOS SN32F290 HAL: bit 4 = USB, bit 5–10 = CT16B0–5,
bit 12/13 = SPI0/1, bit 16–19 = UART0–3, bit 20/21 = I2C1/0, bit 23 = RTC, bit 24 = WDT, bit 26 = LCD,
bit 27 = CRC. `[V-C]`

**`IVTM` is the interesting one.** Enumerated values: `0` = Map to Boot ROM, `1` = User ROM 1, `2` = Map
to SRAM, `3` = User ROM 2, `4` = User ROM 3, `5` = User ROM 4. `[V]` This is how the chip supports
multiple user-ROM banks — the SN32F290 has the 256 KB split into up to four mappable ROMs, which is
exactly the mechanism a jumploader would use.

### 1.7 Flash controller

`SN_FLASH` @ `0x40062000`, 32 bytes:

| Offset | Register | Fields |
| --- | --- | --- |
| `0x00` | `LPCTRL` | `LPMODE[5:0]`, `FMCKEY[31:16]` (verify key) |
| `0x04` | `STATUS` | `BUSY[0]`, `ERR[2]` |
| `0x08` | `CTRL` | `PG[0]`, `PER[1]`, `MER[2]`, `START[6]`, `CHK[7]` |
| `0x0C` | `DATA` | Program data |
| `0x10` | `ADDR` | Address |
| `0x14` | `CHKSUM` | `UserROM[15:0]`, `BootROM[31:16]` |
| `0x18` | `CHKSUM1` | `UserROM1`, `UserROM2` |
| `0x1C` | `CHKSUM2` | `UserROM3`, `UserROM4` |

`[V]` Note the `CHKSUM.BootROM` field — direct hardware evidence that the boot ROM is a distinct,
checksummable region, which corroborates "ROM is separate from user flash."

There is **no flash wait-state field in `SN_FLASH`** in the SVD. Either wait states are configured
elsewhere (`SN_SYS0`?) or the part runs 0-wait at 48 MHz. **`[?]` — I could not determine the flash wait
state configuration.** This matters because it is the classic cause of "runs at 8 MHz, hangs at 48 MHz".
Mitigation: bring the chip up on the internal RC oscillator first and raise the clock only after the
firmware is proven — see §5.

---

## 2. Prior art and licensing

### 2.1 Is the SN32F290 supported? Yes — better than the public docs suggest

The SonixQMK docs' MCU table lists only SN32F248/248B/268, and the compatible-keyboard list contains no
F290 board. `[V-C]` **But the docs are behind the code.** Reading the actual repositories:

**SonixQMK/qmk_firmware** contains an explicit SN32F290 MCU target. `[V-C]`
`platforms/chibios/mcu_selection.mk`, lines 1058–1088:

```
ifneq ($(findstring SN32F299F, $(MCU)),)
  MCU = cortex-m0
  ARMV = 6
  MCU_FAMILY = SN32
  MCU_SERIES = SN32F290
  MCU_LDSCRIPT ?= SN32F290
  MCU_STARTUP ?= sn32f29x
  BOARD ?= SN_SN32F290
  SN32_BOOTLOADER_ADDRESS = 0x1FFF0301
endif
```

and a real keyboard using it: `keyboards/handwired/onekey/sn32f290/keyboard.json` declares
`"processor": "SN32F299F"`, `"bootloader": "sn32-dfu"`. `[V-C]` There is a second one at
`keyboards/handwired/splittest/sn32f290/`. So the F290 path is *exercised by QMK's own CI keyboards*.

**ChibiOS-Contrib** (`sn32_develop`, which is what QMK uses as its `lib/chibios-contrib`) has a
first-class SN32F290 port. `[V-C]`

| Artefact | Path |
| --- | --- |
| Linker script | `os/common/startup/ARMCMx/compilers/GCC/ld/SN32F290.ld` |
| Vendor register header (334 KB) | `os/common/ext/SONiX/SN32F2xx/SN32F290.h` |
| Platform HAL | `os/hal/ports/SN32/SN32F290/{hal_lld.c,hal_lld.h,hal_efl_lld.c,sn32_sys1.h,sn32_registry.h,platform.mk}` |
| Shared peripheral drivers | `os/hal/ports/SN32/LLD/SN32F2xx/{USB,GPIO,CT,SPI,I2C,UART,ADC,RTC,WDT,I2S,SysTick}` |

The shared LLD drivers mean **a working USB device driver for this IP already exists in C**
(`LLD/SN32F2xx/USB/hal_usb_lld.c` + `sn32_usb.h`). That is the single most valuable reusable artefact for
us: it is a working, tested reference implementation of the exact USB controller we would have to drive
from Rust.

### 2.2 What is missing

- No shipping/commercial SN32F290 keyboard in the SonixQMK compatibility list or the
  Mechanical-Keyboard-Database. So the F290 support is real but **thinly exercised** — one-key test
  boards, not a 98-key RGB tri-mode LCD keyboard. `[V-C]`
- The Mechanical-Keyboard-Database ships SVDs for `SN32F240`, `SN32F240B`, `SN32F260` — **not**
  `SN32F290`. `[V-C]` (I obtained the F290 SVD elsewhere; see §3.1.)
- No stock-firmware archive for an F290 board — the `stockFWs/` tree only has `240`, `240B`, `260`
  directories. Our own dump is the only F290 image we have. `[V-C]`

### 2.3 Licensing — this is favourable for an MIT project

| Component | Licence | Can we use it in MIT code? |
| --- | --- | --- |
| **ChibiOS-Contrib** (incl. `SN32F290.ld`, HAL, LLD) | **Apache-2.0** | **Yes** — permissive, MIT-compatible. Attribution + NOTICE required. |
| **Vendor `SN32F290.h`** | ARM CMSIS-style: *"This file can be freely distributed. Modifications to this file shall be clearly marked."* | **Yes, permissive** — mark modifications. |
| **`SN32F290.svd`** | CMSIS-SVD data, no explicit licence; `SVDConv`-generated header carries the permissive ARM notice | Treat as permissive data; attribute Sonix. |
| **QMK** (`qmk_firmware`) | **GPL-2.0** | **No** — copying code in makes our project GPL-2.0. |
| **`SonixFlasherC`** | **GPL-3.0** | **No** — copying code in makes our project GPL-3.0. |
| **`sonix-keyboard-bootloader`** | No LICENSE file in the repo | **No** — all rights reserved by default. Read-only reference. |

**Practical consequence for an MIT-licensed project.** You can:

- **Use** ChibiOS-Contrib's linker script values, register map, and HAL *as technical reference*, and
  re-express them. Apache-2.0 permits derivation and redistribution with attribution.
- **Use** the vendor SVD directly to generate a PAC — the notice permits free distribution.
- **Use** the ISP protocol facts (`SonixFlasherC`'s command set is a *protocol*, and our own `sonix-rs`
  is already an independent implementation — that is clean).
- **Not copy** QMK or `SonixFlasherC` source into an MIT crate. Reading them to learn the register
  sequences is fine; verbatim copying is not.

`sonix-keyboard-bootloader` having **no licence at all** is a real trap — the code is publicly readable
but legally unlicensed, so it must be treated as reference-only, not as a source to port from.

---

## 3. Rust toolchain and crates

### 3.1 Toolchain

Local `rustc` is **1.97.1** (2026-07-14). `thumbv6m-none-eabi` is an available standard target but **is
not currently installed** — `rustup target list --installed` shows only `x86_64-pc-windows-msvc`. First
step is `rustup target add thumbv6m-none-eabi`. `[V]`

`thumbv6m-none-eabi` is the correct and only target for Cortex-M0. `[V]`

**Cortex-M0 limitations that actually bite:**

- **No `LDREX`/`STREX`.** ARMv6-M has no atomic read-modify-write. Consequently
  `core::sync::atomic::AtomicU32::fetch_add` etc. are **not available**; only load/store atomics
  (`AtomicBool`, `AtomicU8`…`AtomicU32` with `load`/`store`) are. `[V]` (architectural)
- **No hardware divide, no `CLZ` in hardware** (emulated by the compiler). `[V]`
- **No unaligned access support in hardware.** `[V]`
- **No `dmb`/`dsb`-heavy data structures without care** — fine for a keyboard.
- `cortex-m`'s `critical-section` implementation has to fall back to disabling interrupts, which is
  correct here but means no `critical_section` nesting guarantees beyond that.
- Crates that gate on `target_has_atomic = "32"` **will not build**. This is the most common surprise.

### 3.2 crates.io reality check — run against the live index

| Crate | Latest | Licence | Verdict |
| --- | --- | --- | --- |
| `usb-device` | 0.3.2 | MIT | **Needed.** Class-agnostic USB device stack. |
| `usbd-hid` | 0.10.2 | MIT OR Apache-2.0 | **Needed.** HID class, descriptor macros. |
| `usbd-human-interface-device` | 0.6.1 | MIT | Optional. Batteries-included keyboard; useful as a shortcut, less control. |
| `cortex-m` | 0.7.9 | MIT OR Apache-2.0 | **Needed.** |
| `cortex-m-rt` | 0.7.7 | MIT OR Apache-2.0 | **Needed.** Startup + vector table. |
| `embedded-hal` | 1.0.0 | MIT OR Apache-2.0 | **Needed.** For GPIO/matrix traits. |

**SN32F2xx PAC/HAL crate on crates.io: DOES NOT EXIST — but generating one is a solved, verified step.** `[V]`

- `cargo search sn32` → only `sn3218`/`sn3218-hal` (an unrelated LED driver chip).
- `cargo search sn32f2` → **zero results.**
- `cargo search sonix` → `sn8flash` 1.0.7, *"A flash tool for Sonix SN8F5xxx family of 8051-compatible
  microcontrollers"* — the 8051 line, **not** SN32. Unrelated.
- No `sn32f290`, no `sonix-rs`-style PAC, no HAL.

#### The PAC, generated and compiled — verified end to end

I did not stop at "the SVD looks parseable". I ran the whole chain during this research:

| Step | Result |
| --- | --- |
| `rustup target add thumbv6m-none-eabi` | installed |
| `cargo install svd2rust --locked` | `svd2rust v0.37.1` installed |
| `svd2rust -i SN32F290.svd --target cortex-m` | exit 0; **`lib.rs`, 4 895 244 bytes, 55 897 lines** (4 950 476 after the inner-attribute strip in `fix-pac.py`) |
| `cargo build --target thumbv6m-none-eabi` | **`Finished dev profile` — exit 0** |
| API smoke test hitting the registers we actually need | **compiles** |

`[V]` — all run locally; artefacts in `research/pac-crate/`.

The smoke test ([smoke.rs](<REPO_ROOT>/research/pac-crate/src/smoke.rs)) is the meaningful part: it
proves the *shapes we need* exist, not merely that bytes were generated.

```rust
pub unsafe fn enter_isp(p: &Peripherals) -> ! {
    p.sn_sys0.ivtm().write(|w| w.bits(0));       // the recovery register, §4
    let go: extern "C" fn() -> ! = core::mem::transmute(ROM_ISP_ENTRY);
    go()
}
```

Resolved successfully: `sn_sys0.{clkcfg,pllctrl,csst,ahbcp,rstst,swdctrl,ivtm}`,
`sn_sys1.{ahbclken,apbcp0,apbcp1,prst}`, `sn_usb.{inten,insts,instsc,addr,cfg,sgctl,ep0ctl,ep1ctl,eptoggle,frmno}`,
`sn_flash.{lpctrl,status,ctrl,addr,data,chksum}`, `sn_gpio0..3.data`, `sn_lcd.ctrl`, `sn_uc.{l4byte,h4byte}`.

API notes learned the hard way: registers are reached by **accessor methods**, not fields
(`p.sn_sys0.ivtm()`, *not* `p.sn_sys0.ivtm`); `Peripherals` fields are snake_case (`sn_sys0`, `sn_usb`);
and write-only registers correctly expose no `.read()` (`SN_USB.INSTSC`). `[V]`

`svd2rust` also emits **`device.x`** (and a `build.rs` to link it): 32 `PROVIDE(<IRQ> = DefaultHandler)`
lines covering every interrupt the SVD declares. That is a small but important freebie — the
`cortex-m-rt` feature gate and default-handler wiring for our 32 IRQs is handled, not something to
hand-write. `[V]`

#### Three real defects in the vendor SVD — all fixed and scripted

The F290 SVD is **not** directly consumable. Three defects, each of which stops codegen dead:

| # | Defect | Symptom | Fix |
| --- | --- | --- | --- |
| 1 | CPU block omits `<mpuPresent>` and `<fpuPresent>`, which CMSIS-SVD requires | `Expected a <mpuPresent> tag, found none` | Add both as `false` (Cortex-M0 has neither) |
| 2 | One `<enumeratedValues>` block is present but **empty** | `EnumeratedValues is empty` | Delete the empty block |
| 3 | ~5 900 enumerated-value `<name>`s are not valid Rust identifiers — they are *descriptions* reused as names (`P2.0`, `0.50*VCC`, `is CMP0V+isLessThan V`, and an enum variant literally called **`New`**, which collides with svd2rust's own `FieldWriter::new`) | `svd2rust` **panics**: `"is_p2.0" is not a valid Ident` | Sanitise every name to a unique valid identifier, reserving svd2rust's generated method names |

`[V]` — the patch is [patch-svd.py](<REPO_ROOT>/research/patch-svd.py), ~40 lines of substance, and
it operates on a **copy** (`SN32F290.patched.svd`); the pristine vendor file is untouched. One extra
wrinkle for anyone reproducing this: `svd2rust` emits a leading run of inner attributes (`#![no_std]`
etc.) which are illegal inside an `include!`d file, so they must be stripped and re-declared in the crate
root — see [fix-pac.py](<REPO_ROOT>/research/fix-pac.py).

**So the plan is:** `svd2rust` → `sn32f290-pac` (MIT/Apache-2.0), then a hand-written `sn32f290-hal` for
the pieces we actually need (SYS0/SYS1 clocks, GPIO, USB). **Do not** try to auto-generate a HAL — the PAC
is 5 MB of raw register access and that is the right granularity for it.

### 3.3 Minimal `memory.x`

Derived from ChibiOS `SN32F290.ld` and confirmed against the stock image:

```
MEMORY
{
  FLASH : ORIGIN = 0x00000000, LENGTH = 256K
  RAM   : ORIGIN = 0x20000000, LENGTH = 32K
}

/* The last 4 bytes of flash (0x3FFFC..0x3FFFF) are the code-option / security word.
   Do not let the linker place anything there. */
```

Notes:

- The ChibiOS linker script additionally defines a dedicated `.flag` section at `0x3FFFC`. A Rust build
  should simply **reserve** that word (shrink `FLASH` to `256K - 4`, or keep a padded region) rather than
  place code over it.
- **No bootloader offset is needed.** Unlike SN32F260 (where the ISP bootloader lives in user flash and
  firmware is linked at `0x200`), the SN32F290 uses the `0x1FFF0000` boot ROM. QMK's SN32F290 target
  links at `0x00000000` with the full 256 K. `[V-C]`
- `cortex-m-rt`'s default vector table is 48 entries (16 system + 32 IRQ), and the SN32F290's highest IRQ
  is **GPIO0 = 31**. So **the default `cortex-m-rt` vector table size is exactly right** — no custom
  vector table work required. `[V]` (SVD IRQ list)

---

## 4. RECOVERY: how do we get back into ISP mode with custom firmware?

**This is the section that decides whether the project is safe.**

### 4.1 The answer: the transition is performed by the application, and it jumps to ROM

I proved this from the stock image. There is no room for interpretation left.

**Step 1 — the magic is compared in application code.** The 8 magic bytes sit at file offset `0x19FC`:

```
0x19FC: aa 42 89 5a ff 71 62 cc  ->  LE u32: 0x5A8942AA  0xCC6271FF
```

They are *data*, and they are **referenced by executable code**, not dead. Scanning the whole image for
Thumb-1 `LDR (literal)` instructions that resolve to a literal pool entry of `0x5A8942AA` / `0xCC6271FF`
finds them at two sites:

```
0x188A: ldr r1, [pc, #368]  ; = 0x5A8942AA     0x1974: ldr r1, [pc, #132]  ; = 0x5A8942AA
0x188C: cmp  r0, r1                            0x1976: cmp  r0, r1
0x188E: beq  0x18C8                            0x1978: beq  0x1982
0x1890: ldr  r0, [...]      ; = 0xCC6271FF     0x197A: ldr  r0, [...]
0x1892: ldr  r1, [pc, #364] ; = 0xCC6271FF     0x197C: ldr  r1, [pc, #128]  ; = 0xCC6271FF
0x1894: cmp  r0, r1                            0x197E: cmp  r0, r1
0x1896: beq  0x18C8                            0x1980: beq  0x1946
```

`[V]` (disassembled from the image). **This is our handshake, found in the vendor firmware.** Note the
byte order confirms our HID transport: the two little-endian words `0x5A8942AA` and `0xCC6271FF` are
exactly the byte sequence `AA 42 89 5A FF 71 62 CC` that we send.

**Step 2 — the take-ISP path.** At `0x18C8`, reached when both words match:

```
0x18C8: movs r4, #4
0x18CA: strb r4, [r5, #18]        ; state = 4  (entering ISP)
0x18CC: ldr  r4, [pc, #280]       ; = 0x200012DC
0x18CE: ldr  r0, [r4, #16]
0x18D0: ldr  r5, [pc, #308]       ; = 0x00010000
0x18D2: lsls r0, r0, #25
0x18D4: beq  0x18F0
...
0x18DE: ldrb r0, [r4, #12]
0x18E0: strb r0, [r6, #12]
...
0x18EA: ldr  r0, [r4, #16]
0x18EA: bic  r0, r5
0x18EC: str  r0, [r4, #16]
0x18EE: b    0x16B2
```

`[V]` — this unmasks/depowers things, clears a state machine, and branches back into the main loop/state
machine at `0x16B2` with state 4 set. The actual ISP jump then happens elsewhere — including, further
up, at the ROM mask/unmask site near `0x18A8`:

```
0x18A8: movs r0, #1
0x18AA: ldr  r1, [pc, #344]       ; = 0xE000E180   <- Cortex-M0 NVIC ICER0
0x18AC: lsls r0, r0, #19
0x18AE: str  r0, [r1, #0]         ; disable IRQ19
0x18B0: bl   ...                  ; subsystem shutdown
0x18B4: bl   ...
0x18B8: bl   ...
0x18BC: bl   ...
0x18C0: bl   ...
0x18C4: bl   ...                  ; <- this is the branch that reaches 0x148
```

`0xE000E180` is the ARMv6-M NVIC Interrupt Clear-Enable Register 0 — **another independent proof this is
ARM, not 8051.** `[V]`

**Step 3 — the actual ROM jump. This is the money shot.** The one function that *is* a pure "go to ISP"
primitive is at `0x148`:

```
0x014C: 01 48     ldr  r0, [pc, #4]   ; = 0x40060024   <- SN_SYS0.IVTM
0x014E: 01 60     str  r1, [r0, #0]   ; *IVTM = r1
0x0150: 01 48     ldr  r0, [pc, #4]   ; = 0x1FFF0301   <- boot ROM entry
0x0152: 00 47     bx   r0             ; jump
0x0154:           0x40060024          <- literal: SN_SYS0.IVTM
0x0158:           0x1FFF0301          <- literal: ROM entry point
```

`[V]` (exact bytes read from the image at 0x014C–0x015B).

I decoded the instruction encodings by hand to be certain:

- `0x014C` = `0x4801` → `01001 000 00000001` = `LDR (literal)`, Rt=0, imm8=1 → `r0 = *(0x0154)` = `0x40060024`.
- `0x014E` = `0x6001` → `0110 0 00000 000 001` = `STR (immediate, T1)`, imm5=0, Rn=0, Rt=1 → `*(u32*)r0 = r1`.
- `0x0150` = `0x4801` → `r0 = *(0x0158)` = `0x1FFF0301`.
- `0x0152` = `0x4700` → `0100 0111 0 000 000` = `BX`, Rm=0 → `bx r0`.

**So the recovery primitive is exactly four instructions, and we have it verbatim:**

```asm
ldr r0, =0x40060024   @ SN_SYS0.IVTM
str r1, [r0]          @ write vector-table mapping
ldr r0, =0x1FFF0301   @ SN32F290 boot ROM entry (Thumb)
bx  r0                @ go
```

### 4.2 Independent confirmation of `0x1FFF0301`

This is not a one-off inference. Four separate sources agree:

1. **Our firmware's own literal pool** at `0x0158`. `[V]`
2. **SonixQMK QMK**, `platforms/chibios/mcu_selection.mk`: the `SN32F299F` block sets
   `SN32_BOOTLOADER_ADDRESS = 0x1FFF0301`. `[V-C]`
3. **QMK's `sn32_dfu.c`** does exactly `void (*recovery)(void) = (void *)SN32_BOOTLOADER_ADDRESS;
   recovery();` — i.e. QMK's own recovery path is "jump to that address", with no IVTM write at all. `[V-C]`
4. **`sonix-keyboard-bootloader`** uses `0x1FFF0301` for every **SN32F240B**-class target and
   `0x1FFF0009` for every SN32F260-class target. The SN32F290 groups with the 240B, not the 260. `[V-C]`
   This is structurally consistent: the SN32F26x is the one part where the ISP is in *user* flash, and it
   has a different entry point.

### 4.3 Is the boot ROM separate from user flash? — Yes

- Our 256 KB image contains **no** code at any `0x1FFFxxxx` address; that is a different memory region. `[V]`
- `sonix-keyboard-bootloader`'s README states outright: *"Some chips in the SN32F2 MCU family have the
  ISP bootloader exposed in userspace. A jumploader guarding it is needed to avoid bricking by erasing
  the bootloader. Affected list: **sn32f26x**."* — i.e. **only** F26x. `[V-C]`
- Its documented memory layout puts `0x1FFF0000 original bootloader` as a separate region alongside
  `0x20000000 ram space`. `[V-C]`
- Hardware corroboration: `SN_FLASH.CHKSUM` has a distinct `BootROM[31:16]` checksum field, and there is
  an `SN_UC` peripheral *inside* the `0x1FFF` region at `0x1FFF2450`. `[V]`
- The F290 ISP reports 256 × 1 KB pages = 256 KB, matching user flash exactly, with the boot ROM outside
  that count. `[V]`

**Conclusion:** on the SN32F290 the ISP bootloader is ROM-resident and **cannot be erased by a firmware
write**. This is the single most reassuring fact in this report. It is also why the F290 does *not* need
SonixQMK's jumploader — that workaround exists for the F26x.

### 4.4 What this means for us — stated explicitly

**Yes: the transition is app-performed, and our custom firmware must implement it as a safety net.**

Concretely, our firmware must include, from the very first flash:

1. **A reachable "jump to ROM" primitive**, byte-identical in effect to the stock one:
   ```rust
   const SN_SYS0_IVTM: *mut u32 = 0x4006_0024 as *mut u32;
   const ROM_ISP_ENTRY: u32 = 0x1FFF_0301;
   unsafe fn enter_isp() -> ! {
       cortex_m::interrupt::disable();          // stock code disables NVIC
       SN_SYS0_IVTM.write_volatile(0);          // IVTM = 0 -> map to Boot ROM
       let f: extern "C" fn() -> ! = core::mem::transmute(ROM_ISP_ENTRY);
       f();
       unreachable!()
   }
   ```
   Note `0x1FFF0301` is odd, which is correct — Thumb entry.

   **Caveat on the `IVTM` write.** The SVD declares `IVTM` as two fields: `IVTM[2:0]` and a write-only
   `IVTMKEY[31:16]` labelled *"IVTM register key"*. `[V]` A key field means the register is very likely
   **write-protected** — writing `0` into the low half with a zero key may simply be ignored. I could
   **not** determine the key value (§6.14). QMK's recovery path skips the IVTM write altogether and
   jumps straight to `SN32_BOOTLOADER_ADDRESS`, which is evidence the write is not required — but treat
   that as evidence, not proof. If the IVTM key matters, the place to find it is the callee at `0x8065`
   that immediately precedes the store.

2. **At least two independent triggers for it**, because one is a single point of failure:
   - **HID feature-report magic**, the same `AA 42 89 5A FF 71 62 CC` on report ID `0x00`, 65-byte
     feature report. This preserves compatibility with our existing
     [sonix-rs](<REPO_ROOT>/sonix/) recovery tool and with the vendor updater.
   - **A power-on key-hold check** — sample a matrix row/column combination *before* USB is up, and if
     held, jump to ROM. This is the mechanism `sonix-keyboard-bootloader` recommends as its primary
     entry ("*it does not rely on a valid firmware being present*"). It costs a handful of GPIO writes
     and a delay, and it works even if USB is completely broken. **This is the highest-value line of
     code in the whole firmware.**

3. **Also honour the QMK convention** so existing tooling works: write `0xDEADBEEF` to the last word of
   RAM and `NVIC_SystemReset()`, with the reset handler checking for it. `[V-C]` QMK's `sn32_dfu.c`.
   This is free and makes our firmware compatible with `qmk`'s `bootloader_jump`.
   *(Careful: on SN32F260 the equivalent magic lives at a fixed RAM address. On F290 QMK uses
   `__ram0_end__ - 4`. Use QMK's convention.)*

### 4.5 The `BOOT` pin — **UNKNOWN for the SN32F290. This is the gap.**

- The community documents `BOOT` → GND at power-up for **SN32F248B (GPIO2 pin 2)** and **SN32F260
  (GPIO3 pin 5)**. `[V-C]` (`sonix-keyboard-bootloader/src/config.h`)
- **I could not find the SN32F290's `BOOT` pin assignment** in the vendor SVD (SVDs do not carry pin
  muxing), nor in ChibiOS-Contrib, nor in QMK. `[?]`
- **I could not find any S98Pro teardown, schematic, or board photo** establishing whether a BOOT pad is
  accessible. `[?]`
- Note also that `SN_SYS0.SWDCTRL.SWDDIS` exists — debug-pin disable is a real, controllable feature on
  this part, and code security (the `0xAAAA5555` word at `0x3FFFC`) is a separate mechanism. `[V]`

This gap is the reason §5 orders the work the way it does. **Do not flash custom firmware until a
hardware recovery path has been proven by reading the chip over SWD.**

### 4.6 Does the ROM auto-enter ISP on a bad image?

**Unknown.** `[?]` The community jumploader enters the bootloader if the reset vector's stack pointer is
invalid, and SonixQMK's stock-firmware note says a botched erase self-recovers into ISP for the families
it covers — **but that is the *jumploader* doing it, not the ROM.** `[V-C]` I found no evidence that the
SN32F290 ROM itself validates the user image at power-up.

**Do not rely on this.** Treat "erase the app and the ROM rescues us" as unproven, because it is.

---

## 5. Recommended first firmware and safe procedure

### 5.1 The safe ordering — hardware recovery FIRST

The instinct is "build a blinking keyboard, flash it, see what happens". That is the wrong order, because
it spends our only proven recovery path before we have a second one.

**Phase 0 — establish recovery before writing a line of firmware. (No flashing.)**

1. **Back up.** Verify [S98Pro_SN32F290_stock.bin](<REPO_ROOT>/keyboard/stock/S98Pro_SN32F290_stock.bin)
   and its `.hex` twin; hash them; keep two copies on different media. Confirm the `.hex` round-trips to
   a byte-identical binary.
2. **Read-only ISP interrogation.** Enter ISP via the known-good magic, run the *read-only* command set
   (`0x21 GetVersion` + `0x26 GetChecksum` only), and record: chip family (expect 32), chip version
   (expect 5 → F290), ROM size (256 KB), ROM pages (256), **code-security level**, and flash checksum.
   Do it **twice** and confirm the checksum is stable. Record the ISP-mode VID/PID. Do not send erase,
   program, or code-option commands.
3. **Confirm the exit path** (`0x07`, `AA 55 00`) restores normal typing. Repeat entry/exit a few times
   so we know the round trip is reliable and not a one-shot.
4. **Open the case and identify the die marking and the debug header.** Look for: the LQFP with the
   Sonix/HFD/eVision marking; a `SN32F290.h`-style pinout match; **any `BOOT`/`GND` test pad**; and a
   4-pin SWD header or 4 test vias (`3V3`, `GND`, `SWCLK`, `SWDIO`).
5. **Fit and prove SWD.** Attach an ST-Link (or equivalent) with `BOOT` strapped as documented for
   whatever pin we identify, and **read the chip ID and at least one flash word**. This is the whole
   point of Phase 0: it converts "we hope we can recover" into "we have read this chip over SWD". If SWD
   read is blocked by code security, that is itself a critical finding and it **changes the plan** — it
   means the ISP route is the *only* route, and the risk profile is much worse.

**Do not proceed to Phase 1 unless step 5 succeeds.** If it does not, the correct move is to stop and
reassess, not to flash anyway.

**Phase 1 — "Flash Key 1": the smallest firmware that proves nothing is lost.**

The smallest *worthwhile* first firmware is not a keyboard. It is an **ISP handoff occupying the whole
of flash**: on reset, wait ~1 second (so we can see the device re-enumerate), then jump to the ROM. Its
only job is to answer one question — *"does a firmware built by our toolchain boot on this chip, and is
it recoverable?"*

Why this is the right first artifact:

- It exercises the entire chain that can fail: `svd2rust` PAC builds → `cortex-m-rt` vector table is
  correct → `memory.x` is correct → the chip runs our code → clock bring-up works → **recovery works**.
- It is **safe by construction**: its normal behaviour *is* entering ISP mode. A bug in it cannot lock us
  out unless it prevents the CPU reaching the jump at all — and even then, Phase 0's SWD path covers us.
- It is small enough to review completely, line by line, before flashing.
- Bring it up on the **internal RC oscillator** first. Do not touch the PLL until the chip is known to
  run our code — this sidesteps the undetermined flash-wait-state question (§1.7) entirely for the
  riskiest first flash.

Sketch:

```rust
#![no_std]
#![no_main]
use cortex_m_rt::entry;
use cortex_m::asm;

const SN_SYS0_IVTM: *mut u32 = 0x4006_0024 as *mut u32;
const ROM_ISP_ENTRY: u32 = 0x1FFF_0301;

#[entry]
fn main() -> ! {
    // Give the host time to see a disconnect, and give a human time to notice.
    for _ in 0..2_000_000 { asm::nop(); }

    cortex_m::interrupt::disable();
    unsafe { SN_SYS0_IVTM.write_volatile(0) };   // IVTM = 0 -> Boot ROM
    let go: extern "C" fn() -> ! = unsafe { core::mem::transmute(ROM_ISP_ENTRY) };
    go()
}
```

**Deliberate simplification:** this does *not* write the IVTM register with a guessed constant beyond
`0`. The stock firmware writes a value it computed; **I could not determine what that value is** (§6).
QMK's own recovery path skips the IVTM write entirely and just calls the address, which is evidence the
jump works without it. If Flash Key 1 does not enter ISP, the first thing to try is setting
`IVTM = 1` (map to User ROM 1) despite the odd address, and the second is to branch to `0x1FFF0001`
(the ROM's reset vector) instead of `0x1FFF0301`.

**Phase 2 — only if Phase 1 succeeds: the real keyboard.**

Add, in this order, so that recovery always exists before the feature that might need it:

1. **Recovery triggers.** Power-on matrix-key hold → `enter_isp()`. HID feature-report magic
   `AA 42 89 5A FF 71 62 CC` → `enter_isp()`. QMK `0xDEADBEEF` RAM magic → `enter_isp()`.
2. **USB device enumeration** — GPIO/USB clock enable, USB controller bring-up, descriptors. Port
   `hal_usb_lld.c` from ChibiOS-Contrib (Apache-2.0) to Rust.
3. **HID keyboard** — `usb-device` + `usbd-hid` class on top.
4. **Matrix scan** — GPIO only. This is the point at which it is a usable keyboard.
5. *Then* RGB, then the radio link, then the LCD. Each of these is a separate risk decision.

For steps 3–4, `usbd-human-interface-device` gets a working keyboard faster; `usb-device` + `usbd-hid`
gives more control when we need the vendor's 4-interface layout back.

### 5.2 Things that would brick or permanently degrade the device — flag list

| Hazard | Why | Mitigation |
| --- | --- | --- |
| **Full-image write that includes `0x3FFFC`** | Clobbers the code-option/security word (`0xAAAA5555`) | Preserve it; read it with ISP `0x29 Get code option` first; write it back deliberately |
| **Setting code security to CS1/CS2/CS3** | May block SWD readout — destroys the Phase 0 recovery path | **Never** write a non-zero security value during development |
| **`SN_SYS0.SWDCTRL.SWDDIS`** | Can disable the debug pins in hardware | Do not touch; it is a one-way door if the app is broken |
| **Losing the app-performed ISP trigger** | The vendor's entry path dies with the stock app | Flash Key 1 is deliberately ISP-only; every later build keeps ≥2 triggers |
| **Assuming the ROM self-recovers on a bad image** | Unproven (§4.6) | Treat as false until proven |
| **Undetermined flash wait states at 48 MHz** | Classic silent hang | Bring up on RC oscillator; raise the clock only after the chip demonstrably runs our code |
| **External SPI NOR (LCD/GIF assets)** | Not the MCU; a bad write there is unrecoverable from vendor tools | Do not touch `SN_SPI1` / the NOR during firmware work. (I did **not** verify which SPI port or CS the NOR uses on this board — `[?]`) |
| **Erasing before having a known-good image in hand** | Obvious, but the ISP has no read command | Keep the verified stock image and two copies of it |

### 5.3 A note on how to verify recovery *before* flashing anything

The honest version: **you cannot fully verify recovery before flashing, because the thing under test is
"can we recover from custom firmware", and that requires custom firmware.** What Phase 0 buys is not
proof — it is *independence*. After Phase 0 we have two recovery paths (ROM-via-app and SWD), and only
one of them depends on our code being correct. That is the difference between "risky" and "safe", and it
is why step 5 of Phase 0 is a hard gate rather than a nice-to-have.

---

## 6. What I could not determine

Stated plainly, because a clear unknown is worth more than a confident guess.

1. **The `IVTM` value the stock firmware writes before jumping to ISP.** The code at `0x014C` does
   `*0x40060024 = r1`, but `r1` is not resolvable by linear disassembly (it comes from a callee's return
   state). The SVD gives the encoding (`0` = Boot ROM, `1` = User ROM 1, …) but not the constant used.
   *What would confirm it:* a full decompiler pass over the function containing `0x148` (Ghidra is
   available in this environment), or gdb/OpenOCD at the breakpoint.
2. **The SN32F290's `BOOT` pin** — number, port, and active level. Not in the SVD (SVDs carry no pin
   muxing), not in ChibiOS-Contrib, not in QMK. *What would confirm it:* the SN32F290 datasheet's pin
   assignment table (I could not retrieve a readable PDF — Sonix's product pages are JS-rendered and the
   PDF endpoints returned no extractable text from this session), or continuity-testing a suspected pad
   on the real board.
3. **Whether the S98Pro exposes a BOOT pad, SWD header, or SWD vias.** No teardown, schematic, or
   board photo found. *What would confirm it:* opening the case.
4. **Flash wait states at 48 MHz.** `SN_FLASH` has no wait-state field in the SVD. *What would confirm
   it:* the datasheet's flash-timing section, or empirical stepping of the clock.
5. **The FMC's on-die page-erase granularity** versus the ISP's advertised 1 KB pages.
6. **Whether `0xAAAA5555` at `0x3FFFC` is a security level, a code option, or both** — it is not in
   `SonixFlasherC`'s security table in either byte order.
7. **What exactly `0x1FFF0301` points at inside the boot ROM** (a 4-byte-aligned address that is not the
   reset vector, never called — only `bx`'d). All four independent sources agree it is *the* entry
   address, so this is a "what is it" curiosity, not a risk.
8. **Whether the SN32F290 ROM validates the user image at power-up** and self-enters ISP (§4.6).
9. **Whether the PLL can reach 48 MHz** — the SVD says the *CPU clock* is up to 48 MHz but I did not
   confirm the PLL/clock-source configuration needed to get there, nor the maximum flash clock.
10. **Which SPI port and chip-select the external LCD NOR uses** on this board.
11. **`SN_LCD` register semantics.** The stock firmware clearly drives it, but the SVD gives register
    names only, and the LCD's role versus the external NOR is unresolved.
12. **The licence of `sonix-keyboard-bootloader`.** No `LICENSE` file exists in the repository, so it is
    unlicensed — usable as reading material, not as a source to port from.
13. ~~Whether `svd2rust` completes on this SVD.~~ **Resolved — it does.** Generated a 4.9 MB PAC that
    compiles clean for `thumbv6m-none-eabi`; see §3.2. (Superseded; kept here so the change of state is
    visible rather than silently edited away.)
14. **The exact `IVTM` key.** The SVD declares a write-only `IVTMKEY[31:16]` field alongside `IVTM[2:0]`,
    which implies the register is key-protected — some value must occupy the upper half-word for a write
    to take effect. **I did not determine that key.** This is the most likely reason a naive
    `IVTM = 0` write would be ignored. *What would confirm it:* the function at `0x8065` in the stock
    image (called immediately before the IVTM store at `0x014C`), or the datasheet's IVTM section.
    Note QMK's recovery path skips the IVTM write entirely, which is evidence the ROM jump works without
    it — so this may be moot, but it is not proven.

---

## Sources

### Primary — vendor and official

1. **Sonix SN32F290 CMSIS-SVD** — `https://chipselect.org/SONIX--SN32F290.svd` — the vendor register
   database. Declares `CM0 r0p0`, `nvicPrioBits 2`, CPU up to 48 MHz; 35 peripherals, 422 registers.
   Downloaded and parsed locally as `research/SN32F290.svd`.
2. Sonix 32-bit MCU selection guide — `https://www.sonix.com.tw/download/MCU.pdf`
3. Sonix SN32F280/290 series product page — `https://www.sonix.com.tw/article-jp-4797-39755`,
   `https://www.sonix.com.tw/article-jp-4797-39756` (JS-rendered; no extractable register data)
4. Sonix SN32F290 series document endpoint —
   `https://www.sonix.com.cn/files/1/A9EB5EFFCFB4987EE050007F010036AC?mirror=cn` (returned no text)
5. Sonix SN32F100 datasheet (Cortex-M0 + FMC registers), mirrored by Keil —
   `https://www.keil.com/dd/docs/datashts/sonix/sn32f100/sn32f100.pdf`

### Primary — read directly out of our own hardware's firmware

6. `<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin` (262144 bytes) — vector table at
   `0x0000`, ISP magic at `0x19FC`, magic compare at `0x188A`/`0x1974`, ISP jump at `0x014C`, NVIC ICER
   reference at `0x18AA`, code-option word at `0x3FFFC`.
7. Analysis scripts written for this report, in `<REPO_ROOT>\research\`: `fw-scan.py`,
   `decode-jump.py`, `dis.py`, `verify-isp.py`, `find-magic-ref.py`.

### Community prior art

8. **SonixQMK/ChibiOS-Contrib**, branch `sn32_develop` — `https://github.com/SonixQMK/ChibiOS-Contrib`
   — Apache-2.0. `os/common/startup/ARMCMx/compilers/GCC/ld/SN32F290.ld` (256 K flash / 32 K RAM /
   `_flag_start = 0x3FFFC`); `os/common/ext/SONiX/SN32F2xx/SN32F290.h` (334 KB, `SVDConv V3.3.35`,
   generated from `SN32F290.svd`, last modified 2020-09-01); `os/hal/ports/SN32/SN32F290/` HAL;
   `os/hal/ports/SN32/LLD/SN32F2xx/{USB,GPIO,CT,SPI,I2C,UART,...}`.
9. **SonixQMK/qmk_firmware** — `https://github.com/SonixQMK/qmk_firmware` — GPL-2.0.
   `platforms/chibios/mcu_selection.mk:1058-1088` (SN32F299F → SN32F290,
   `SN32_BOOTLOADER_ADDRESS = 0x1FFF0301`); `platforms/chibios/bootloaders/sn32_dfu.c` (RAM-magic +
   reset, then jump to `SN32_BOOTLOADER_ADDRESS`);
   `keyboards/handwired/onekey/sn32f290/keyboard.json` (`"processor": "SN32F299F"`).
10. **SonixQMK/SonixFlasherC** — `https://github.com/SonixQMK/SonixFlasherC` — **GPL-3.0**.
    `include/chip.h` (`ROM_SIZE_F290 256`, `ROM_PAGES_F290 256`, `CHIP_F290 5`, CS levels);
    `src/chip.c` (family-version 32 → `CHIP_F290` when chip version = 5).
11. **SonixQMK/sonix-keyboard-bootloader** — `https://github.com/SonixQMK/sonix-keyboard-bootloader` —
    **no licence** (all rights reserved). `src/main.c`, `src/config.h` (`0x1FFF0301` for SN32F240B-class,
    `0x1FFF0009` for SN32F260-class; `BOOT0` = GPIO2.2 and GPIO3.5), `README.md` (only SN32F26x has the
    ISP in user flash; memory layout).
12. SonixQMK Docs — `https://sonixqmk.github.io//SonixDocs/`,
    `.../compatible_kb/`, `.../faq/` (MCU table lists F248/F248B/F268 only — behind the code)
13. **SonixQMK/Mechanical-Keyboard-Database** — `https://github.com/SonixQMK/Mechanical-Keyboard-Database`
    — SVDs for SN32F240/240B/260 (no F290); `stockFWs/` for 240/240B/260 (no F290).

### Rust ecosystem

14. `usb-device` 0.3.2 (MIT) — `https://github.com/rust-embedded-community/usb-device`
15. `usbd-hid` 0.10.2 (MIT OR Apache-2.0) — `https://github.com/twitchyliquid64/usbd-hid`
16. `usbd-human-interface-device` 0.6.1 (MIT) — `https://github.com/dlkj/usbd-human-interface-device`
17. `cortex-m` 0.7.9, `cortex-m-rt` 0.7.7 (MIT OR Apache-2.0) — `https://github.com/rust-embedded/cortex-m`
18. `embedded-hal` 1.0.0 (MIT OR Apache-2.0) — `https://github.com/rust-embedded/embedded-hal`
19. `sn8flash` 1.0.7 — Sonix **SN8F5xxx (8051)**, unrelated to SN32. Listed only to document that it is
    *not* a match.

### Related work in this workspace

20. [PROTOCOL.md](<REPO_ROOT>/sonix/PROTOCOL.md) — our ISP protocol notes
21. [ISP-ENTRY-PROTOCOL.md](<REPO_ROOT>/keyboard/ISP-ENTRY-PROTOCOL.md),
    [ISP-ENTRY-SOLVED.md](<REPO_ROOT>/keyboard/ISP-ENTRY-SOLVED.md),
    [ISP-ENTRY-LIVE-TEST.md](<REPO_ROOT>/keyboard/ISP-ENTRY-LIVE-TEST.md) — the live entry/exit work
22. [SONIX-RESEARCH.md](<REPO_ROOT>/keyboard/SONIX-RESEARCH.md) — earlier platform research
23. [GHIDRA-FINDINGS.md](<REPO_ROOT>/keyboard/GHIDRA-FINDINGS.md),
    [GHIDRA-HANDOFF.md](<REPO_ROOT>/keyboard/GHIDRA-HANDOFF.md) — prior static analysis

### Artefacts produced by this report (in `<REPO_ROOT>\research\`)

| File | What it is |
| --- | --- |
| `SN32F290.svd` | Vendor CMSIS-SVD, 2 083 508 bytes — source of the whole register map (pristine, unmodified) |
| `SN32F290.patched.svd` | The same SVD with the three codegen-blocking defects fixed (§3.2) |
| `patch-svd.py` | Script that produces the patched SVD |
| `pac-crate/` | **A working `sn32f290-pac` crate**: `svd2rust` output that compiles for `thumbv6m-none-eabi` |
| `pac-crate/src/pac.rs` | The generated PAC (5.0 MB, 55 897 lines) |
| `pac-crate/device.x` | `PROVIDE(<IRQ> = DefaultHandler)` for all 32 IRQs — `cortex-m-rt` wiring, generated for free |
| `pac-crate/src/smoke.rs` | Compile-time proof the PAC exposes the registers we need, incl. the IVTM recovery write |
| `fix-pac.py` | Strips the inner attributes that make `svd2rust` output illegal inside `include!` |
| `ChibiOS-Contrib/` | Cloned prior art (Apache-2.0) — contains the `SN32F290` linker script and HAL |
| `qmk_firmware/` | Cloned prior art (GPL-2.0) — contains the `SN32F299F` MCU target |
| `SonixFlasherC/` | Cloned prior art (GPL-3.0) — chip table and ISP command set |
| `sonix-keyboard-bootloader/` | Cloned prior art (unlicensed — reference only) |
| `mkdb/` | Mechanical-Keyboard-Database clone |
| `fw-scan.py`, `decode-jump.py`, `dis.py`, `verify-isp.py`, `find-magic-ref.py` | Read-only analysis scripts for the stock image: flash map, Thumb-1 disassembly, ISP-jump decoding, magic cross-reference |
| `pac-crate/target/` | Build output (safe to delete) |
