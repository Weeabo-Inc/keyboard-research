# S98Pro Matrix and GPIO

> # ⚠️ SUPERSEDED — THIS DOCUMENT CONTAINS ERRORS
>
> **Read [COMPANION-BUS.md](COMPANION-BUS.md) instead.** Two conclusions here are wrong, and
> both would break firmware built on them.
>
> **1. "There is no GPIO key matrix" is WRONG.** There *is* a 6-row × 19-column matrix. It was
>    missed for two independent reasons:
>    * the scan is **not a loop** — `FUN_00003352` @ `0x003352` is a 22-way `switch` compiled to
>      `bl __ARM_common_switch8`, which Ghidra decompiles into a garbage indirect call
>    * the pins become OUTPUTS inside `FUN_000034C8` @ `0x0034C8`, which runs on **every sample**,
>      not in the init function examined here. That is why "all ports MODE=0" was both true and
>      misleading.
>
>    The proof is a mask correspondence: `FUN_000034C8` does `GPIO0_MODE |= 0xFF`,
>    `GPIO1_MODE |= 0x40001`, `GPIO2_MODE |= 0x43FF`, and those are **exactly** the union of the
>    switch's BSET masks — 8 + 2 + 11 = 21 pins.
>
> **2. Every pin number derived from the `lsls rX,#n / bpl` idiom is WRONG.** That idiom tests
>    bit **(31 − n)**, not bit *n*. This affects the row pins, the `FUN_00001ef0` mode-switch
>    pins, and the ISR pin lists. The correct rows are:
>
>    ```
>    b0 GPIO1.14   b1 GPIO1.15   b2 GPIO1.19
>    b3 GPIO3.19   b4 GPIO0.19   b5 GPIO0.18
>    ```
>
> **Also wrong:** `FUN_00005174` writes its samples to `0x2000135B`, not `0x20000095`.
>
> **Still valid here, and worth keeping:** the GPIO register semantics and base addresses, the
> `CFG`-resets-to-pull-up-OFF landmine, and the general shape of the debounce pipeline. Those
> were checked against the SVD and hold.

Reverse-engineering note on `keyboard/stock/S98Pro_SN32F290_stock.bin` (256 KB, SN32F290,
Cortex-M0), loaded in Ghidra as program `S98Pro_SN32F290_stock.bin`.

**Read-only analysis. No hardware touched, nothing flashed, nothing modified.**

Every address below is a *file/flash offset* (the image is loaded flat at `0x00000000`, so
Ghidra addresses == file offsets — verified by comparing Ghidra's memory block to the raw
bytes).

---

## Summary

### The headline: there is no GPIO key matrix scan in this firmware

This is not a hedged conclusion. The evidence is direct and mutually consistent:

| Evidence | Where |
|---|---|
| All four GPIO ports are initialised **input-dominant**: `MODE` = `0x00000000` on all four ports in pass 1 — GPIO0 later gains only pin 15 as an output (`MODE |= 0x8000`) | `FUN_00007800` @ `0x007800`, disasm `0x007812`/`0x007822`/`0x00782A`/`0x007838`, then `0x0078AE` |
| There is **no row-drive / column-sense loop anywhere**. A scan would need outputs toggled per row; only a handful of pins are ever outputs | full-image sweep of all 43 GPIO literals |
| The key-state pipeline reads a **19-byte array from a bit-banged 3-wire serial link** to an off-chip controller, not from GPIO | `FUN_000034ee` @ `0x0034EE` (read) + `FUN_000043e4` @ `0x0043E4` (clock bitbang) |
| The only GPIO **outputs** actually driven are the bit-bang pins (`GPIO3` pins 5/6) | `0x0043F4`–`0x004418` |
| No key/scan/matrix strings exist in the image at all | ASCII sweep ≥5 chars |

The stock firmware's architecture is **"Sonix SN32F290 as USB-HID + LCD + RGB bridge, key
sensing done by a separate controller"**, which matches the platform research in
[SONIX-RESEARCH.md](SONIX-RESEARCH.md) §1.4 (radio module on a serial link).

**Consequence for the safety net — read this before designing it:** the "hold a key at
power-on" trick cannot be derived from this firmware's *scan* code, because there is no scan
code. What *can* be derived is the complete, exact **GPIO initialisation sequence** (verbatim
below, §4) and the set of **pins the MCU actually senses** (§2). The safety net must detect a
held key as *"one of those sensed pins is asserted at boot"* — and that pin→key mapping is the
one thing this firmware does **not** contain. §6 gives a self-calibrating design that does not
depend on knowing the mapping, plus the measurement that closes the gap.

### The pins that matter, as the firmware configures them

Values are the **final** 32-bit register writes made by `FUN_00007800`. All ports are
initialised before any key event can be processed (`FUN_00007800` is the 4th call in the main
`FUN_000006d6` @ `0x0006D6`, and the first three are clock/USB setup).

| Port | MODE (0x04) | Meaning | CFG (0x08) | CFG1 (0x30) | Pull-up state after init | Confidence |
|---|---|---|---|---|---|---|
| **GPIO0** | `0x00008000` | pin 15 **output**; pins 0–14 input | `0x00004000` | `0x0000A000` | pins 0–6, 8–15 ON; pin 7 field `0b01` | **High** |
| **GPIO1** | `0x00000000` | **all input** | `0x20000000` | `0x0000800A` | **pins 0–13, 15 ON**; 14 OFF; 16 OFF; 17 ON; 18 OFF; 19 ON | **High** |
| **GPIO2** | `0x00000000` | all input | `0x00000000` | not written | **pins 0–15 ON** | **High** |
| **GPIO3** | `0x00008000` | pin 15 **output**; all else input | `0x00004000` | `0x00008000` | pins 0–6, 8–15 ON; pin 7 field `0b01`; 16–22 ON; 23+ n/a | **High** |

The per-pin `CFG` view is the operational one. Decoding the four 32-bit values, 2 bits per pin
(`bitOffset = 2 * (pin % 16)`, `0b00` = pull-up, `0b10` = input+schmitt, `0b11` = analog):

| Port | CFG (pins 0–15) | CFG1 (pins 16–19) |
|---|---|---|
| GPIO0 | `0x00004000` → pin 7 field `0b01` (undefined); **pins 0–6, 8–15 = `0b00` (pull-up)** | `0x0000A000` → pin 13 `0b00` (pull-up), pin 14 `0b10` (schmitt) |
| GPIO1 | `0x20000000` → **CFG bits 28–29, i.e. pins 14–15 of a *32*-pin port.** On a 20-pin port this hits nothing; pins 0–13 stay `0b00` (pull-up) | `0x0000800A` → pin 16 `0b10` (schmitt), pin 17 `0b00` (**pull-up**), pin 18 `0b10`, pin 19 `0b00` (**pull-up**) |
| GPIO2 | `0x00000000` → **pins 0–15 all pull-up** | untouched (reset `0b10` = schmitt, no pull-up) |
| GPIO3 | `0x00004000` → pin 7 field `0b01`; **pins 0–6, 8–15 = pull-up** | `0x00008000` → pin 18 `0b10`; **pin 19 `0b00` (pull-up)** — bits 8–31 select nothing (`CFG1` defines only `CFG16..CFG19`) |

> **On `GPIO1_CFG = 0x20000000`:** a 20-pin port has no pins 14/15 *in this register* — those pin
> numbers live in `CFG1`. Taken literally the write lands on bits 28–29, which on GPIO1 select
> nothing (the port tops out at pin 19). Ignore it; do not "fix" it by copying it. (The vendor's
> clear intent was almost certainly to hit `CFG1` bits 28–29 for pins 14–15, and they wrote `CFG`
> instead — the same off-by-one-register mistake as A1.)
>
> **On `GPIO2 = all pull-up`:** the stock firmware writes `GPIO2_CFG = 0` but never writes
> `GPIO2_CFG1`, so GPIO2 **pins 16–19 keep the reset value `0b10` (schmitt, no pull-up)**. If
> your net reads pins 16–19 of any port, write `CFG1 = 0` yourself.
>
> **Correction applied.** An earlier draft showed `GPIO0_CFG1 = 0x5000`, `GPIO1_CFG = 0xA0000000`,
> `GPIO1_CFG1 = 0x800A`, `GPIO1_MODE = 0xA0000000` and `GPIO0_CFG = 0x4000` (final). Those came
> from a hand-rolled Thumb decoder used during this analysis whose format-8/9 split is **buggy**
> (it emits `strh [R,#imm<<1]` where the true instruction is `str [R,#imm<<2]`). Ghidra's
> disassembly is authoritative and disagrees; the values above are Ghidra's. See §7 (A9).
>
> The `GPIO0_CFG = 0x00004000` write at `0x0078A2` **overwrites** the `GPIO0_CFG = 0` at
> `0x00780A` in the same function. The *final* state is what these tables describe.

### The pins the MCU actually senses (interrupt-enabled inputs)

| Port | Interrupt-enabled pins | `IE` bits | `IEV` (0x14) | `IS` (0x0C) | ISR | ISR clears (`IC`, 0x20) |
|---|---|---|---|---|---|---|
| **GPIO0** | **10, 19** | `0x000C0400` | `0x000C0400` | `0x000000FF` | `0x00000C67` (IRQ31) | `0x000C0400` |
| **GPIO1** | **1, 2, 14, 15** | `0x0008C006` | `0x0008C006` | 0 (level) | `0x00000E4D` (IRQ30) | `0x0008C006` |
| **GPIO2** | **10, 11, 12, 13** | `0x00003C00` | `0x0008C006` | 0 | `0x00000C89` (IRQ29) | `0x0003C00`† |
| **GPIO3** | **8, 9, 10, 11, 12, 13, 14, 15, 16** | `0x00073F00` | `0x00073F00` | 0 | `0x00000CAB` (IRQ28) | `0x00080000` |

† the GPIO2 ISR writes a constant `0x80000`, **not** the `0x3C00` mask — see §7 (anomaly A2).

`IEV` is *interrupt event* (edge polarity); `IS` selects level vs edge. `IS = 0xFF` on GPIO0
means **pins 0–7 are level-sensitive**, pins 8–19 edge-sensitive.

**Confidence, column by column:** the `ISR` and `ISR clears (IC)` columns are **High** — they are
immediates in the handlers. The `IE`/`IEV` columns are **Medium** — `FUN_00007800` writes those
registers more than once with different values and I could not fully order the writes (§7 A2),
and the GPIO2 `IEV` cell is a known bad read. **The safest subset to trust is the ISR-clear
masks**, which give:

| Port | Pins the ISR confirms can fire |
|---|---|
| GPIO0 | **10, 19** (`0x000C0400`) |
| GPIO1 | **1, 2, 14, 15** (`0x0008C006`) |
| GPIO2 | 19 only (`0x00080000`) |
| GPIO3 | 19 only (`0x00080000`) |

These 19 interrupt inputs (as configured) are the only electrical key candidates on the MCU.
`0xC0400` (GPIO0) + `0x8C006` (GPIO1) + `0x3C00` (GPIO2) + `0x73F00` (GPIO3) together span
**28** pins, while the consumer pipeline is 19 bytes wide — see §7 (A3).

---

## 1. Matrix dimensions

**I could not determine a GPIO matrix dimension, because the MCU does not scan a matrix.**
Stated plainly rather than guessed: the requested "how many rows and columns" has no answer in
this firmware.

What is determinable with evidence:

| Quantity | Value | Evidence | Confidence |
|---|---|---|---|
| Physical key count | 98 keys + 1 rotary encoder | product layout; not in firmware | High (external fact) |
| **Key-state records consumed per scan** | **19** | `FUN_000032e0` @ `0x003348` loops `uVar8 < 0x13` over three parallel 19-byte arrays | **High** |
| Parallel key-state arrays | 3 × 19 B + event buffer 19 B | `0x20001348` (raw), `0x2000135B` (prev), `0x2000136E` (counter), `0x2000016E` (events) | High |
| Debounce depth | **3 consecutive equal samples** | `if (2 < (byte)(cVar3+1U))` after `cVar3 = counter++` @ `0x003348` | **High** |
| Non-key sensed inputs | 3-bit mode/switch value | `FUN_00001ef0` @ `0x001EF0` reads GPIO1 pin 1, GPIO1 pin 2, GPIO0 pin 10; `FUN_00001ec6` @ `0x001EC6` seeds a 20-count window | **High** |
| MCU-side matrix rows/cols | **0 / 0** (none) | §Summary evidence | **High** |

If a row/column count is still needed for documentation purposes: 19 key-state slots for a
98-key board is consistent with a **19-column matrix scanned off-chip** (e.g. 6 rows × 17
cols + encoder, or 6 × 16 + extras). **That is inference, not measurement** — do not build on it.

### 1.1 The debounce pipeline (how keys are actually processed)

`FUN_000032e0` @ `0x003348` — three 19-byte arrays based at:

| Address | Role |
|---|---|
| `0x20001348` | raw (current) sample, 19 bytes |
| `0x2000135B` | previous accepted state, 19 bytes (= `0x20001348 + 0x13`) |
| `0x2000136E` | per-input stability counter, 19 bytes (= `0x20001348 + 0x26`) |
| `0x2000016E` | last-change (event) buffer, 19 bytes |
| `0x2000016D` | event write index |
| `0x20000170` | event/queue structure used by the report builder |

Logic (decompiled, `0x003348`):

```c
for (uVar8 = 0; uVar8 < 0x13; uVar8++) {          /* 19 inputs */
  bVar1 = raw[i];                                  /* 0x20001348 + i        */
  bVar2 = prev[i];                                 /* 0x2000135B + i        */
  if (bVar1 == bVar2) {
      if (counter[i] != 0) {
          if (++counter[i] > 2) {                  /* 3 stable samples      */
              counter[i] = 0;
              uVar6 = last[i] ^ bVar2;             /* 0x2000136E + 0x13 + i */
              if (uVar6) { index = i; mask = bVar2; last[i] = bVar2;
                           FUN_00003292(); }
          }
      }
  } else { prev[i] = bVar1; counter[i] = 1; }
}
```

`FUN_00003292` @ `0x003292` then packages `{index, mask}` into the event buffer at
`0x2000016E`, which `FUN_000002f8` @ `0x0002F8` drains into USB reports. `FUN_000032e0` is
called from the main loop `FUN_000006d6` when a flag byte is set, and from `FUN_000061e4`.

### 1.2 Where the 19 raw bytes come from — the off-chip link

`FUN_000034ee` @ `0x0034EE` is the single-sample reader. It calls `FUN_000034c8` (setup) and
`FUN_00003352`, then packs six bits of one GPIO port into a 6-bit field:

```c
bVar1  = (*DAT_000035e8 << 0x11 < 0);            /* GPIO1_DATA bit 15  -> b0 */
if (*DAT_000035e8 << 0x10 < 0) bVar1 |= 2;       /* GPIO1_DATA bit 14  -> b1 */
if (*DAT_000035e8 << 0x0c < 0) bVar1 |= 4;       /* GPIO1_DATA bit 11  -> b2 */
if (*DAT_000035f4 << 0x0c < 0) bVar1 |= 8;       /* GPIO3_DATA bit 11  -> b3 */
if (*DAT_000035e0 << 0x0c < 0) bVar1 |= 0x10;    /* GPIO0_DATA bit 11  -> b4 */
if (*DAT_000035e0 << 0x0d < 0) bVar1 |= 0x20;    /* GPIO0_DATA bit 10  -> b5 */
```

(The `<<n` / sign-bit idiom is `(x >> (31-n)) & 1`. Literal pool `0x0035E0` = `0x40044000`
(GPIO0), `0x0035E8` = `0x40046000` (GPIO1), `0x0035F4` = `0x4004A000` (GPIO3).)

`FUN_00005174` @ `0x005174` calls this **19 times** into `0x20000095`:

```c
for (i = 0; i < 0x13; i++) { *(byte*)(0x20000095 + i) = FUN_000034ee(); }
```

**This is a multi-wire parallel interface, not a bit-banged serial read** — six MCU pins are
sampled per "sample", 19 samples per scan. That is a direct, parallel bus from an off-chip
keyboard controller (or an external row-driver/expander that delivers key state over 6 lines).
I label the *direction* (MCU ← controller) as **High** confidence and the exact external part
as **unknown** — see §7.

Separately, `FUN_000043e4` @ `0x0043E4` **is** a genuine 3-wire bit-bang on GPIO3:

```c
/* r2 = 0x4004A000 (GPIO3), r6 = 0x40046000 (GPIO1) */
for (i = 0; i < 8; i++) {
    if (msb) GPIO3_BSET = 0x40; else GPIO3_BCLR = 0x40;   /* pin 6 = data out */
    bit = (GPIO1_DATA >> 22) & 1;                          /* pin 22 = data in */
    GPIO3_BSET = 0x20;                                     /* pin 5 = clock    */
    result = (result << 1) | (bit | msb);
    GPIO3_BCLR = 0x20;                                     /* clock low        */
}
```

`FUN_0000482e` @ `0x00482E` uses it to read a register addressed `7`, then write register
`0x27` — i.e. an external SPI/serial peripheral with a 0x27 control register. This is the RGB /
LCD / radio companion path, **not** the key path.

---

## 2. Pin assignments (the table)

This is the complete, machine-verified inventory of GPIO pins the firmware touches. Derived by
enumerating **all 43 literal-pool words that land in a GPIO block** and resolving every
subsequent `[base + offset]` access (see §Evidence E1/E2).

### 2.1 Configured by `FUN_00007800` (the master init)

| Pin | Direction (MODE) | Pull-up (CFG) | Interrupt | Role |
|---|---|---|---|---|
| GPIO0 pin 0–6 | input | **ON** | — | driven by off-chip controller |
| GPIO0 pin 7 | input | field `0b01` (undefined) | — | see §7 (A1) |
| GPIO0 pin 8, 9 | input | **ON** | IEV + IE | input |
| GPIO0 pin **10** | input | **ON** | IEV `0x400` + IE | one of the 3 mode/switch bits |
| GPIO0 pin 11 | input | **ON** | IEV + IE | input |
| GPIO0 pin 12–14 | input | **ON** | — | input |
| GPIO0 pin **15** | **output** | none | IEV `0x8000` + IE `0x8000` | contradictory — §7 (A1) |
| GPIO0 pin 16–19 | input | CFG1 default | pin **19**: IEV + IE | input |
| GPIO1 pin 0 | input | ON | IE `0x1` | input (never fires — §7 A2) |
| GPIO1 pin **1** | input | ON | IEV `0x2` + IE | **mode/switch bit 0** |
| GPIO1 pin **2** | input | ON | IEV `0x4` + IE | **mode/switch bit 1** |
| GPIO1 pin 3–4 | input | ON | pin 4: IE `0x10` | input |
| GPIO1 pin 5 | input | ON | IE `0x20` | input |
| GPIO1 pin 14 | input | ON | IEV `0x4000` + IE | input |
| GPIO1 pin **15** | input | ON | IEV `0x8000` + IE | **mode/switch bit 2**; also sampled by `FUN_000034ee` |
| GPIO1 pin 18 | input | ON | IE `0x40000` | input |
| GPIO1 pin 22 | input | ON | — | **bit-bang data in** (`FUN_000043e4`) |
| GPIO2 pin 0–9 | input | ON | IE `0x3FF` | input |
| GPIO2 pin 10–13 | input | ON | IEV `0x3C00` + IE | input |
| GPIO2 pin 14 | input | ON | IE `0x4000` | input |
| GPIO3 pin 5 | input* | ON | — | **bit-bang clock out** (`GPIO3_BSET/BCLR = 0x20`) |
| GPIO3 pin 6 | input* | ON | — | **bit-bang data out** (`GPIO3_BSET/BCLR = 0x40`) |
| GPIO3 pin 8, 9 | input | **OFF** | IEV + IE | externally driven input |
| GPIO3 pin 10–12 | input | **OFF** | IEV + IE | externally driven input; pins 11 also sampled by `FUN_000034ee` |
| GPIO3 pin 13–16 | input | **OFF** | IEV + IE | externally driven input |
| GPIO3 pin 18–22 | input | ON | — | pin 22 = bit-bang data **in** |
| — (unreachable) | — | the `CFG`/`CFG1` writes at `0x00780E` (`0xA000`) and `0x007832` (`0x8000`) select pin numbers ≥ 29, which do not exist — no-op writes, see §7 A1 |

\* marked input because `MODE` says input — **the `MODE` bits for pins 5 and 6 are never set to
output even though the code drives them via `BSET`/`BCLR`.** Either `MODE` is not the direction
control on this part for those pins, or this is a firmware bug that happens to work. Flagged as
§7 (A4) — **do not copy this pattern**.

### 2.2 Is there a pin-definition table?

**No.** A matrix scan normally indexes a `const` array of `{port, pin, mask}` entries. The
firmware contains four small **base-pointer** tables and **no pin table**:

| Address | Contents | Used by |
|---|---|---|
| `0x000011450` | `GPIO0, GPIO1, GPIO2, GPIO3` bases (4 words) | `0x011198`–`0x0111C2` |
| `0x0000035E0` | `GPIO0, 0x40001, GPIO1, 0x43FF, GPIO2, GPIO3` | `FUN_000034c8` / `FUN_000034ee` |
| `0x0000047E0` | `GPIO3, GPIO1, GPIO0` | `FUN_000043e4` |
| `0x0000078BC` | `GPIO0, GPIO1, 0x800A, GPIO2, GPIO3, 0xFFFF, 0x73F00, 0x40001, 0x43FF` | `FUN_00007800` |

The constants are the only "pin definition" data present, and they are **masks**, not pins:
`0x00073F00` (GPIO3 IE), `0x00040001` (GPIO1 IE/IEV), `0x000043FF` (GPIO2 IE/IEV).

---

## 3. Scan method

| Question | Answer | Confidence |
|---|---|---|
| Drive-low-and-read, or drive-high-and-read? | **Neither.** No row drive exists. Key state arrives as 19 parallel multi-bit samples over 6 GPIO input lines | **High** |
| Diodes? | Not applicable to the MCU. Any diodes are behind the off-chip controller | n/a |
| Separate drive / sense phases? | No. One phase: sample 6 lines → one byte; 19 samples → full scan | **High** |
| Debounce approach | **3-sample** counter per input (count must reach 3 with an unchanged raw value), then edge detection by XOR against the previously accepted state, emitting per-input change events | **High** |
| Scan cadence | No periodic timer ISR scans keys. `FUN_000032e0` runs from the main loop `FUN_000006d6` when a flag is set, and from `FUN_000061e4` (which delays `FUN_00006f1a(500)` between passes) | Medium-High |
| Interrupt use | 28 GPIO inputs are interrupt-enabled, but the ISRs only record a bitmask / request mode changes; `FUN_00001ef0` debounces the 3-bit mode value with a 20-count window (`0x14`) | High |

The GPIO ISRs are thin. Example, GPIO0 (IRQ31, `0x00000C66`):

```c
NVIC_ICPR(0x1f);                       /* FUN_0000125e: *0xE000E280 = 1<<31 */
GPIO0_IC = 0x000C0400;                 /* clear the two flags        */
SYS0->[?] |= 0x40000;                  /* FUN_00000c66: ungate clock */
NVIC_ISER(0x1f);                       /* FUN_0000124e: *0xE000E180+0x80 = 1<<31 */
```

(The `DAT_00000f9c + 0x10` write resolves to `0x200012DC + 0x10`, an **SRAM** address — the
decompiler resolved it as `200012DC`, not as `0x40060000`; the clock-gate target is therefore
not confirmed. See §7 (A5).)

GPIO1's ISR (`0x00000E4D`, IRQ30) is the only one that produces key events. It reads
`GPIO1_DATA`, then sets a **single bit of a 32-bit bitmap** via `FUN_0000eeee(bits)`, where the
bitmap write is `*(u32*)(DAT_00000f78 - 0x40) = <mask>` and `DAT_00000f78 = 0x4005C048`
(USB block). The masks written by the per-pin handlers:

| Vector | Handler | `GPIO1_DATA` bit | Bitmap mask |
|---|---|---|---|
| `0x00000DB8` | pin 0 | 0 | `0x1` |
| `0x00000DAE` | pin 1 | 1 | `0x2` |
| `0x00000DA4` | pin 2 | 2 | `0x4` |
| `0x00000D9A` | pin 3 | 3 | `0x8` |
| `0x00000D90` | pin 4 | 4 | `0x10` |
| `0x00000D86` | pin 5 | 5 | `0x20` |
| `0x00000DCE` | pin 12 | 12 | `0x1000` |
| `0x00000DC2` | pin 13 | 13 | `0x2000` |
| `0x00000DE6` | pin 14 | 14 | `0x400` |
| `0x00000DF8` | pin 15 | 15 | `0x200` |
| `0x00000E04` | pin 18 | 18 | `0x100` |

**The `bitmap mask` is not equal to `1 << pin`.** The ISR's decision tree tests one
`GPIO1_DATA` bit at a time in pin order 0,1,2,3,4,5 then 11,12,13,14,15,16,17, but the masks
emitted are `0x1,0x2,0x4,0x8,0x10,0x20` then `0x100,0x200,0x400,0x1000,0x2000`. Bit 6 and bit
7 are skipped, so **the handler at `0x00000D86` is not pin 5** — I could not resolve the exact
pin→mask mapping from the dispatch structure alone. Treat the pin column above as
**Medium** confidence and the mask column as **High** (masks are immediates in the decompiled
handlers). This does not affect any recommendation in §6.

---

## 4. GPIO init sequence (register writes, in order)

`FUN_00007800` @ `0x007800` — called once from the main entry `FUN_000006d6` @ `0x0006D6`
(4th call, after `FUN_00008e26`, `FUN_00008d7c`, `FUN_00000998`). **This is the directly
reusable part.** Values transcribed from Ghidra's disassembly with the literal pool resolved
(no hand-decoding).

```
; --- literal pool (0x0078BC..0x0078DC) ---
; 0x40044000 GPIO0   0x40046000 GPIO1   0x40048000 GPIO2   0x4004A000 GPIO3
; 0x0000FFFF   0x0000800A   0x00073F00   0x00040001   0x000043FF
```

### Pass 1 — reset / establish every port

```
GPIO0_DATA = 0x0000FFFF
GPIO0_CFG  = 0x00000000            ; pins 0..15: CFG field 0b00 = pull-up ON
GPIO0_CFG1 = 0x0000A000            ; pin 13 field 0b00 (pull-up), pin 14 field 0b10
GPIO0_MODE = 0x00000000            ; ALL INPUT

GPIO1_DATA = 0x0000FFFF
GPIO1_CFG  = 0x20000000            ; CFG bits 28..29 = pins 14..15 field 0b10
GPIO1_CFG1 = 0x0000800A            ; pin16 0b10, pin17 0b00, pin18 0b10, pin19 0b00
GPIO1_MODE = 0x00000000            ; ALL INPUT

GPIO2_DATA = 0x0000FFFF
GPIO2_CFG  = 0x00000000            ; pins 0..15 pull-up ON
GPIO2_MODE = 0x00000000            ; ALL INPUT

GPIO3_DATA = 0x0000FFFF
GPIO3_CFG  = 0x00000000            ; pins 0..15 pull-up ON  (partly overwritten later)
GPIO3_CFG1 = 0x00008000            ; pin 23 field 0b00 (pull-up)
GPIO3_MODE = 0x00000000            ; ALL INPUT

; --- pass 1b: MODE |= (0x00783A .. 0x00785C) ---
GPIO0_MODE |= 0x00000B00           ; pins 8, 9, 11
GPIO1_MODE |= 0x00000030           ; pins 4, 5
GPIO2_MODE |= 0x00003C00           ; pins 10, 11, 12, 13
GPIO3_MODE |= 0x00073F00           ; pins 8..16

; --- pass 1c: BCLR = the same masks (0x00785E .. 0x00786E) ---
GPIO0_BCLR = 0x00000B00
GPIO1_BCLR = 0x00000030
GPIO2_BCLR = 0x00003C00
GPIO3_BCLR = 0x00073F00

; --- pass 1d: MODE |= (0x007870 .. 0x007886) ---
GPIO0_MODE |= 0x000000FF           ; pins 0..7
GPIO1_MODE |= 0x00040001           ; pins 0, 18
GPIO2_MODE |= 0x000043FF           ; pins 0..9, 14
GPIO3_MODE |= 0x00000000           ; (read then written back unchanged)

; --- pass 1e: BSET (0x00788C .. 0x007896) ---
GPIO0_BSET = 0x000000FF
GPIO1_BSET = 0x00040001
GPIO2_BSET = 0x000043FF
GPIO3_BSET = 0x00000000

call FUN_0000aeb6()

; --- pass 2: final MODE / BSET for GPIO0 (0x00789C .. 0x0078B4) ---
GPIO0_MODE |= 0x00004000           ; pin 14
GPIO0_BSET  = 0x00004000
GPIO0_MODE |= 0x00008000           ; pin 15
GPIO0_BSET  = 0x00008000
```

**Read the two `MODE`/`BSET` pairs as the vendor's idiom, not as intent.** Passes 1b/1c and 1d/1e
write the *same* masks to `MODE` and to `BSET`/`BCLR`. The only interpretation consistent with
the register map is: `MODE` selects direction, and `BSET`/`BCLR` then force the output **data
latch** for exactly those pins (drive them high, then low). That is a plausible bring-up
sequence. It is *also* possible that only the `MODE` writes are meaningful and the `BSET`/`BCLR`
writes are redundant. **The safe reproduction is: `MODE` for direction, `CFG` for pull, then set
the output levels you actually want via `BSET`/`BCLR`.** Do not blindly replay passes 1b–1e.

### The minimal init you actually need for a safety net

The whole of §4 collapses to this, because the safety net only needs *pulled-up inputs* and a
`DATA` read, and it never needs `MODE`, `BSET`, `BCLR`, or any interrupt register:

```rust
// GPIO0 = 0x4004_4000, GPIO1 = 0x4004_6000, GPIO2 = 0x4004_8000, GPIO3 = 0x4004_A000
// Offsets (32-bit accesses, per the SVD/PAC and the vendor header):
//   0x00 DATA   0x04 MODE   0x08 CFG   0x30 CFG1
for gpio in [GPIO0, GPIO1] {        // GPIO2 optional; see note
    gpio.mode.write(0);             // 0x04 <- 0 : every pin an input
    gpio.cfg.write(0);              // 0x08 <- 0 : CFG field 0b00 = PULL-UP on pins 0..15
    gpio.cfg1.write(0);             // 0x30 <- 0 : pull-up on pins 16..19 too
}
let a = GPIO0.data.read();          // 0x00
let b = GPIO1.data.read();
let idle = read_baseline_from_flash();   // 0xFFFF_FFFF on first ever boot
let held = idle & !(a | b);              // any bit the switch pulled low
```

**Why the `CFG` write is mandatory and why the order matters:** the PAC records each `CFG` field's
**reset value as `0b10`** ("Value on reset: 2") — i.e. **inputs come out of reset with the
pull-up *disabled***. Reading `DATA` without first writing `CFG = 0` reads floating pins and you
will get a random answer. This is the single easiest way to build a safety net that appears to
work on the bench and then silently fails.

Two cautions:

* **`GPIO1_CFG` must be `0` for the pull-ups to be on.** The stock firmware writes
  `GPIO1_CFG = 0x20000000`, which **disables** the pull-up on GPIO1 pins 14 and 15 (and pins 0–13
  keep theirs only because the adjacent fields are `0b00`). If your safety-net key lands on
  GPIO1 pin 14 or 15, the stock firmware's own configuration would leave it floating — another
  reason to write `CFG = 0` yourself rather than replaying `FUN_00007800`.
* `GPIO2_CFG` ends at `0x00000000` in the stock firmware, so **GPIO2 pins 0–15 all have pull-ups**
  and GPIO2 is a clean port to include. `GPIO3` is the one to avoid: its `CFG` ends at
  `0x00004000` and its pins 8–16 are externally driven.

---

## 5. GPIO register semantics (from the SVD)

Bases: `SN_GPIO0 = 0x40044000`, `SN_GPIO1 = 0x40046000`, `SN_GPIO2 = 0x40048000`,
`SN_GPIO3 = 0x4004A000`. Verified three ways: SVD `<baseAddress>`, the vendor header
`SN32F290.h` (`SN_GPIO0_BASE 0x40044000UL`), and the firmware literal pool at `0x0078BC`.

### Register map — `SN32F290.patched.svd`, parsed

| Offset | Name | Access | Purpose |
|---|---|---|---|
| `0x00` | `DATA` | R/W | Port data. `DATA0..DATA19` for pins 0–19 |
| `0x04` | **`MODE`** | R/W | **Direction**: bit *n* = 1 → output, 0 → input |
| `0x08` | **`CFG`** | R/W | **Pull/schmitt** for pins 0–15, 2 bits per pin |
| `0x0C` | `IS` | R/W | Interrupt **sense**: 0 = edge, 1 = level |
| `0x10` | **`IBS`** | R/W | Interrupt **both-edge sense**, 1 bit per pin |
| `0x14` | **`IEV`** | R/W | Interrupt **event** (edge polarity) |
| `0x18` | `IE` | R/W | Interrupt **enable** |
| `0x1C` | `RIS` | RO | Raw interrupt status |
| `0x20` | `IC` | WO | Interrupt **clear** (write 1s) |
| `0x24` | `BSET` | WO | Atomic bit **set** of `DATA` |
| `0x28` | `BCLR` | WO | Atomic bit **clear** of `DATA` |
| `0x30` | **`CFG1`** | R/W | Pull/schmitt for pins **16–19**, 2 bits each (`CFG16..CFG19`) |

⚠ **`0x10` vs `0x14` naming is contested between sources:**

| Source | Offset `0x10` | Offset `0x14` | Correct? |
|---|---|---|---|
| `research/SN32F290.patched.svd` | `IBS` | `IEV` | **assumed correct** (also what the PAC generates) |
| `ChibiOS-Contrib/.../SN32F290.h` | `IBS` | `IEV` | agrees with SVD |
| `ChibiOS-Contrib/.../hal_pal_lld.c` | `IEV` (`port->IEV &= ~(1<<pad)`) | `IE` (`port->IE |=`) | **contradicts both** |

The ChibiOS *driver* is self-consistent: it treats `0x10`=IEV, `0x14`=IE, `0x18`=RIS — but the
SVD and the vendor header place `IE` at `0x18` and `RIS` at `0x1C`. **This is a real, unresolved
discrepancy and a landmine**: if the SVD/PAC is right and the driver is wrong, QMK's SN32 port
has been writing edge polarity into a both-edge register. Since the SVD and the vendor header
agree with each other, **trust the SVD/PAC names**, but see the verification test in §6.3.
**The safety net in §6 does not touch any of these three registers**, so it is immune.

### Field encoding — `CFG` / `CFG1` (2 bits per pin, `bitOffset = 2 * (pin % 16)`)

From the PAC (`s98rs/pac/src/lib.rs`, `sn_gpio0::cfg`) and confirmed by
[hal_pal_lld.c:215-246](research/ChibiOS-Contrib/os/hal/ports/SN32/LLD/SN32F2xx/GPIO/hal_pal_lld.c):

| Value | Driver constant | Effect |
|---|---|---|
| `0b00` | `PAL_MODE_INPUT_PULLUP` | **pull-up resistor enabled** |
| `0b01` | (undefined) | not a valid setting |
| `0b10` | `PAL_MODE_INPUT` | pull-up off, schmitt trigger **enabled** |
| `0b11` | `PAL_MODE_INPUT_ANALOG` | pull-up off, schmitt trigger **disabled** |

So **`CFG = 0x00000000` puts a pull-up on every pin 0–15 of that port.** This is the single most
useful fact in this document for the safety net, and it is confirmed by two independent sources.

**Reset value is `0b10` per pin** (PAC: "Value on reset: 2" for `Cfg0`), i.e. **inputs have no
pull-up out of reset** — you *must* write `CFG = 0` (or the per-pin `0b00` fields) or a floating
pin will read randomly.

### Open-drain

**There is no open-drain field in `MODE`, `CFG`, or `CFG1`** — the SVD enumerates exactly the
fields above and none is an open-drain control. If open-drain is needed it must come from the
pin's alternate-function config (e.g. the I²C block's `I2C0`/`I2C1` pin options), not from GPIO.
**I could not find an open-drain bit for plain GPIO** — do not assume one exists.

### `DATA` is genuinely bidirectional

`MODE = 1` → `DATA` bit drives the pin; `MODE = 0` → `DATA` bit reads the pin. `BSET`/`BCLR`
(`0x24`/`0x28`) are write-only atomic set/clear of the same latch and are the safe way to change
one output without a read-modify-write race. This is the standard Sonix layout and is confirmed
by the vendor driver (`pal_lld_setport` writes `BSET`, `pal_lld_clearport` writes `BCLR`,
`pal_lld_readpad` reads `DATA`).

---

## 6. Recommended safety-net key, and why

### 6.1 The honest constraint

I **cannot** name a specific physical key with confidence, and I will not guess, because you
said a wrong pin is worse than an admitted unknown. The reason is architectural: **the pin→key
mapping does not exist in this firmware.** The MCU receives 19 abstract key-state slots from an
off-chip controller over a 6-line parallel bus; which physical key lands in which slot is
decided on the other side of that bus.

What the firmware *does* tell us, and what is enough to build a **safe** net:

| Fact | Value |
|---|---|
| Which pins are inputs with pull-ups after `MODE=0, CFG=0, CFG1=0` | **all of GPIO0/GPIO1/GPIO2 pins 0–19**; GPIO3 pins 0–6, 8–17, 20–22 |
| Which pins are **driven by external devices** (pull-ups off, expect contention) | **GPIO3 pin 18** — `GPIO3_CFG1 = 0x00008000` leaves pin 18 schmitt (no pull-up). Pins 8–16 keep their pull-ups but are also interrupt-sensed, so treat them as shared |
| Which pins are outputs | GPIO0 pin 15 (`MODE |= 0x8000`) — and nothing else that is provably reachable: the `CFG`/`CFG1` writes at `0x00780E`/`0x007832` address PIN numbers ≥ 29, which no GPIO port has (`CFG1` defines only `CFG16..CFG19`), so those writes select nothing. **There is no real second output pin in ports 0–2** |
| Which pins are used for the bit-bang | GPIO3 pins 5 (clock), 6 (data out), 22 (data in) |
| Port that is cleanest for a safety net | **GPIO2** — inputs only, `CFG = 0`, no bit-bang, no external driver |

### 6.2 Recommendation

**Do not pick one key. Use a self-calibrating "any sensed input differs from the boot baseline"
check across GPIO0, GPIO1 and GPIO2.** This is strictly better than a single key here, because:

* it needs **zero** knowledge of the pin→key mapping, which is exactly the knowledge we lack;
* it cannot be defeated by a wrong guess;
* it costs one register read per port.

```rust
// Runs BEFORE USB init, before clocks, before anything that can fail.
// GPIO0/1/2 all pins input + pull-up.  NEVER touch GPIO3 (pins 8..16 are
// externally driven; a pull-up there fights the driver).
const GPIOS: [(u32, &str); 3] = [
    (0x4004_4000, "GPIO0"), (0x4004_6000, "GPIO1"), (0x4004_8000, "GPIO2"),
];
for (base, _) in GPIOS {
    write32(base + 0x04, 0);   // MODE = 0     -> all input
    write32(base + 0x08, 0);   // CFG  = 0     -> pull-up ON, pins 0..15
    write32(base + 0x30, 0);   // CFG1 = 0     -> pull-up ON, pins 16..19
}
delay_us(2000);                                    // let pull-ups settle

// Baseline: with nothing pressed, capture the idle state once, at first boot,
// and store it in the last flash page.  On later boots, any bit that is LOW
// while the stored baseline says HIGH means "a key is held".
let now  = read32(0x4004_4000) | read32(0x4004_6000) | read32(0x4004_8000);
let idle = read_baseline_from_flash();             // 0xFFFFFFFF on first ever boot
let held = idle & !now;                            // bits pulled low by a switch

if held != 0 || read32(0x4004_4000) & (1 << 15) == 0 {   // GPIO0 pin 15 doubles as a
    enter_rom_isp();                                     // hardware-visible strap
}
```

Design notes:

* **Why the baseline matters.** 28 interrupt inputs are enabled in the stock firmware. Some are
  certainly not keys (mode switch, cable detect, charge status). Without a baseline, "any pin
  low" could fire on a benign condition on every boot and *always* drop you into ISP — which is
  a different kind of brick. If you would rather not store a baseline, use the conservative
  variant: require the pressed bit to be one of the **GPIO0/GPIO1/GPIO2 interrupt-enabled
  pins listed in §Summary**, and require it to persist for ~100 ms, and make ISP entry
  *conditional on a second confirmation* (e.g. keep holding for 3 s).
* **GPIO0 pin 15 is the only deterministic pin available** if you want a strap rather than a
  scanned input: it is the only pin in ports 0–2 that `FUN_00007800` turns into an **output**
  (`GPIO0_MODE |= 0x8000`), and the firmware reads that register back at `0x0114D6`
  (`ldrh r0,[r4,#0x0]` where `r4 = 0x40044000`). However it is also interrupt-enabled on the
  same pin, and it is wired to something with an existing function — **do not repurpose it
  without measuring it first.** (`Caveat:` I could not resolve the enclosing function's entry
  point, so this cites the read *site*, not a function name.)
* **Never use GPIO3 for the safety net.** Pins 8–16 are pull-up-disabled and externally driven,
  and pins 5/6/22 are the bit-banged link to the companion chip. Reading them is fine; enabling
  pull-ups or driving them can corrupt that link.

### 6.3 The one measurement that closes the gap

This is the highest-value 10 minutes you can spend, and it is the *only* way to convert
"candidate pins" into "the key":

1. Flash a **5-line debug build** that does the §6.2 init, then prints `GPIO0/1/2 DATA` over
   the ISP or UART link, or simply lights the RGB under each pin.
2. With **nothing pressed**, record the three `DATA` values → that is the idle baseline
   (this also confirms the pull-ups work).
3. Press and **hold each candidate key** one at a time; record which bit changes.
   The candidate set is small — the interrupt-enabled pins in §Summary: GPIO0 {10, 19},
   GPIO1 {1, 2, 4, 5, 14, 15, 18}, GPIO2 {0…9, 10…14}.
4. The key you want is a bit that goes low on exactly one key, on **GPIO0 or GPIO1**, and that
   sits at a board edge. Then hard-code that single bit in the production build for speed.

While you are there, the same build settles the `IBS`/`IEV` vs `IEV`/`IE` naming question from
§5: write `1 << p` to `0x14` only, then trigger pin *p* and read `RIS` (`0x1C`). If `RIS` reads
as before, the SVD is right; if the pin now triggers on **both** edges, the ChibiOS driver's
naming is right and the PAC is wrong.

---

## 7. What I could not determine

Listed explicitly, in descending order of how much they could hurt.

**A1 — `FUN_00007800` contains at least one genuine bug, so my transcription may faithfully
reproduce broken behaviour rather than intent.** Four specific problems:

* `GPIO0_MODE |= 0x8000` makes pin 15 an **output** while `GPIO0_IEV |= 0x8000` and
  `GPIO0_IE |= 0x8000` also enable an **interrupt** on the same pin — contradictory.
* `GPIO0_CFG = 0x00004000` and `GPIO3_CFG = 0x00004000` set the **undefined** field value `0b01`
  on pin 7 of each port. At `0x00789E` the code computes `asrs r0, r5, #1` with `r5 = 0x8000`,
  giving `0x4000` — but the *pin 15* `CFG` field is `0x4000_0000`. **The vendor almost certainly
  meant to clear the pin-15 pull-up and shifted by the wrong amount.**
* `GPIO1_CFG = 0x20000000` sets the undefined `0b01`-style pattern on GPIO1 pins 14–15.
* `GPIO2_IEV` is written as `0x00080000` (`0x0034BE`), which is a `RIS`-style value, not an `IEV`
  mask — consistent with the `0x0078BC`-pool idiom being misapplied.

Either this firmware build is not the exact S98Pro build, or the vendor shipped this. **I could
not determine which.** Cross-check by dumping the same offsets from a second SN32F290 keyboard
firmware if you have one — the sibling F108 Pro is the obvious candidate. **Do not treat
`FUN_00007800` as a reference implementation**; use §4's minimal init instead.

**A2 — `FUN_00007800` writes several registers more than once with different values, and I could
not fully order them.** Specifically `GPIO3_IE` is written `0x00073F00` and
`GPIO2_IE`/`GPIO1_IE` are written `0x000043FF`/`0x00040001` in separate passes; the GPIO2 ISR
clears `0x00080000` but the `IE` mask everyone can see is `0x3C00`. Since GPIO3's `IE` ends at
`0x00073F00` (matching the value at `0x0051C4` used by the CT16B3 handler at `0x0050BE`), the
final state is *probably* the union, but I am not confident. **Consequence:** the exact set of
interrupt-enabled pins may be a superset of §Summary. The safety net does not depend on it.

**A3 — 28 interrupt-enabled pins vs 19 key-state slots.** I could not reconcile these. Either
~9 of those pins are not keys (most likely — mode switch, charge status, RF module lines), or
the 19-byte pipeline is fed a subset. **Open.**

**A4 — GPIO3 pins 5 and 6 are driven via `BSET`/`BCLR` while `MODE` says input.** I could not
determine whether `MODE` is the direction control for these pins on this silicon. Note that
`GPIO3_CFG = 0x00004000` leaves **pins 8–16 without pull-ups**, so those are loaded by external
drivers — relevant if you ever reconfigure GPIO3. **Do not copy the "drive without setting
MODE" pattern.**

**A5 — the clock-gate register written by the ISRs is unresolved.** `FUN_00000c66` does
`*(u32*)(DAT_00000f9c + 0x10) |= 0x40000` and the literal at `0x000F9C` is `0x200012DC`, an
**SRAM** address — so Ghidra resolved this as a RAM write, yet the same idiom appears at
`0x0034B2`–`0x0034C4` as a plain `IE` write. The intended peripheral is probably `SN_SYS0`
(`0x40060000`); I could not confirm which register.

**A6 — the external key controller is unidentified.** It is on the other end of the 6-line bus
sampled by `FUN_000034ee` (`GPIO0` bits 10/11, `GPIO1` bits 11/14/15, `GPIO3` bit 11) and/or the
3-wire bit-bang on GPIO3 (clock pin 5, data-out pin 6, data-in pin 22) that reads register `7`
and writes register `0x27` of something. Whether that is the RF module, an RGB/LCD controller, a
keyboard matrix controller, or two different chips is **unknown**. IDing it would require a
teardown or a logic-analyser capture, and it is the single thing that would definitively resolve
the pin→key mapping.

**A7 — is this the S98Pro's actual firmware?** Independently flagged in
[S98PRO-FIRMWARE.md](S98PRO-FIRMWARE.md) §"Open question". The flasher config (`Sonix_flasher_UISettings.ini`)
lists `PID=0x8009` and `0x8801` for `SN32F290.hex`, while the keyboard enumerates as
`0C45:800A`. The file is named `S98PRO Firemware.exe`, which is suggestive but not proof.
**The GPIO init values (`0x00073F00`, `0x00040001`, `0x000043FF`, `0x0000800A`) are
board-specific enough that this is very likely the right build**, but I cannot prove it from the
image alone. If it is a sibling board, every pin assignment in §2 is suspect while §4/§5
(the init *pattern* and the register semantics) remain valid.

**A8 — open-drain.** No open-drain field exists in `MODE`/`CFG`/`CFG1` per the SVD. If a
replacement firmware needs open-drain GPIO, that must come from the I²C alternate functions.
I could not find a plain-GPIO open-drain control.

**A9 — my own Thumb decoder is buggy and nearly produced wrong pin data.** The helper
`research/tools/thumbdis.py` mis-decodes load/store immediate offsets: it returns
`strh rX,[R,#imm<<1]` where the true instruction is `str rX,[R,#imm<<2]`. Concretely, for the
bytes at `0x00780A` the decoder said `strh r3,[r4,#4]` while the instruction is
`str r3,[r4,#0x8]`. **That is a difference between writing `MODE` and writing `CFG`** — i.e.
between an input pin with no pull-up and a floating one. Ghidra's disassembly is correct, and
§4/§Summary were corrected against it. The decoder is left in-tree because its *literal-pool
sweep* is sound and is what produced the §Evidence E2 inventory, but **do not use its load/store
text as ground truth.** Always confirm with `ghidra_mcp` `disassemble_function`.

---

## Evidence

### E1 — GPIO base addresses, three independent sources

| Source | GPIO0 | GPIO1 | GPIO2 | GPIO3 |
|---|---|---|---|---|
| `SN32F290.patched.svd` `<baseAddress>` | `0x40044000` | `0x40046000` | `0x40048000` | `0x4004A000` |
| `research/ChibiOS-Contrib/.../SN32F290.h:4786-4789` | `0x40044000UL` | `0x40046000UL` | `0x40048000UL` | `0x4004A000UL` |
| `s98rs/pac/src/lib.rs:21141,25519,29897,33412` | `0x4004_4000` | `0x4004_6000` | `0x4004_8000` | `0x4004_a000` |
| firmware literal pool `0x0078BC..0x0078CC` | `0x40044000` | `0x40046000` | `0x40048000` | `0x4004A000` |

### E2 — the full GPIO-literal inventory (machine-generated)

43 words in the image fall inside a GPIO register block; every one of them is a **base address**
(`+0x00`), because the firmware always adds the register offset at the instruction:

```
0x0005A8 GPIO1   0x00096C GPIO1   0x000F98 GPIO0   0x000FA4 GPIO1   0x000FA8 GPIO3
0x001294 GPIO1   0x00129C GPIO0   0x002270 GPIO1   0x002278 GPIO0   0x003420 GPIO0
0x003424 GPIO2   0x003428 GPIO1   0x0035E0 GPIO0   0x0035E8 GPIO1   0x0035F0 GPIO2
0x0035F4 GPIO3   0x0047E0 GPIO3   0x0047E4 GPIO1   0x0047E8 GPIO0   0x004C08 GPIO0
0x0051B4 GPIO1   0x0051B8 GPIO3   0x0051BC GPIO2   0x0051C0 GPIO0   0x0062B8 GPIO0
0x0066C8 GPIO3   0x0066CC GPIO0   0x0066E0 GPIO1   0x006730 GPIO0   0x0078BC GPIO0
0x0078C0 GPIO1   0x0078C8 GPIO2   0x0078CC GPIO3   0x007E10 GPIO1   0x007E18 GPIO0
0x007EE0 GPIO0   0x00F2B4 GPIO0   0x010C04 GPIO3   0x011450 GPIO0   0x011454 GPIO1
0x011458 GPIO2   0x01145C GPIO3   0x011768 GPIO0
```

### E3 — the reset/entry chain (confirmed)

```
vector[0] SP           = 0x20002B18        (32 KB SRAM at 0x20000000 -> valid)
vector[1] Reset        = 0x00007EE5  (Thumb) -> 0x00007EE4
vector[3] HardFault    = 0x00000901
vector[11] SVCall      = 0x00007F03
vector[14] SysTick     = 0x00007F05
vector[15]+IRQ0        = 0x00006F51  (SN_SYS0 / NDT,  IRQ0)
IRQ1  USB              = 0x00000E4D*
IRQ6  SPI0             = 0x00007DB7
IRQ8  UART2            = 0x00008C01
IRQ19 CT16B3           = 0x000050BF
IRQ25 WDT              = 0x00007AA9
IRQ28 P3 (GPIO3)       = 0x00000CAB
IRQ29 P2 (GPIO2)       = 0x00000C89
IRQ30 P1 (GPIO1)       = 0x00000E4D
IRQ31 P0 (GPIO0)       = 0x00000C67
0x00007F09            = default/empty handler (shared by 25 slots)
```

\* **IRQ1 (USB) and IRQ30 (GPIO1) are both `0x00000E4D`** — the same address, Thumb bit included.
Either this is a genuine shared handler or the vector table has an entry error. **I could not
determine which.** This matters: it means the "GPIO1 ISR" analysis in §3 may actually be the USB
ISR. Flagged, not resolved.

GPIO interrupt numbers confirmed from the SVD:
`IRQ 28 P3 SN_GPIO3`, `IRQ 29 P2 SN_GPIO2`, `IRQ 30 P1 SN_GPIO1`, `IRQ 31 P0 SN_GPIO0`.

### E4 — `FUN_00007800` disassembly (Ghidra, verbatim, with pool resolved)

```
0x007800  push {r3,r4,r5,r6,r7,lr}
0x007802  ldr r4,[0x000078bc]        ; r4 = 0x40044000 GPIO0
0x007804  ldr r5,[0x000078b8]        ; r5 = 0x0000FFFF
0x007806  str r5,[r4,#0x0]           ; GPIO0_DATA = 0xFFFF
0x007808  movs r3,#0x0
0x00780a  str r3,[r4,#0x8]           ; GPIO0_CFG  = 0
0x00780c  movs r0,#0x5
0x00780e  lsls r0,r0,#0xd            ; 0x5<<13 = 0xA000
0x007810  str r0,[r4,#0x30]          ; GPIO0_CFG1 = 0xA000  (!!)
0x007812  str r3,[r4,#0x4]           ; GPIO0_MODE = 0
0x007814  ldr r0,[0x000078c0]        ; r0 = 0x40046000 GPIO1
0x007816  str r5,[r0,#0x0]           ; GPIO1_DATA = 0xFFFF
0x007818  movs r1,#0x5
0x00781a  lsls r1,r1,#0x1d           ; 0x5<<29 = 0xA0000000
0x00781c  str r1,[r0,#0x8]           ; GPIO1_CFG  = 0xA0000000 (!!)
0x00781e  ldr r1,[0x000078c4]        ; r1 = 0x0000800A
0x007820  str r1,[r0,#0x30]          ; GPIO1_CFG1 = 0x0000800A
0x007822  str r3,[r0,#0x4]           ; GPIO1_MODE = 0
0x007824  ldr r2,[0x000078c8]        ; r2 = 0x40048000 GPIO2
0x007826  str r5,[r2,#0x0]           ; GPIO2_DATA = 0xFFFF
0x007828  str r3,[r2,#0x8]           ; GPIO2_CFG  = 0
0x00782a  str r3,[r2,#0x4]           ; GPIO2_MODE = 0
0x00782c  ldr r1,[0x000078cc]        ; r1 = 0x4004A000 GPIO3
0x00782e  str r5,[r1,#0x0]           ; GPIO3_DATA = 0xFFFF
0x007830  str r3,[r1,#0x8]           ; GPIO3_CFG  = 0
0x007832  movs r5,#0x1
0x007834  lsls r5,r5,#0xf            ; 0x1<<15 = 0x8000
0x007836  str r5,[r1,#0x30]          ; GPIO3_CFG1 = 0x8000
0x007838  str r3,[r1,#0x4]           ; GPIO3_MODE = 0
0x00783a  ldr r6,[r4,#0x4]           ; GPIO0_MODE
0x00783c  movs r7,#0xb
0x00783e  lsls r7,r7,#0x8            ; 0xB00
0x007840  orrs r6,r7
0x007842  str r6,[r4,#0x4]           ; GPIO0_MODE |= 0xB00      (pins 8,9,11)
0x007844  ldr r6,[r0,#0x4]
0x007846  movs r7,#0x30
0x007848  orrs r6,r7
0x00784a  str r6,[r0,#0x4]           ; GPIO1_MODE |= 0x30       (pins 4,5)
0x00784c  ldr r6,[r2,#0x4]
0x00784e  movs r7,#0xf
0x007850  lsls r7,r7,#0xa            ; 0x3C00
0x007852  orrs r6,r7
0x007854  str r6,[r2,#0x4]           ; GPIO2_MODE |= 0x3C00     (pins 10..13)
0x007856  ldr r7,[r1,#0x4]
0x007858  ldr r6,[0x000078d0]        ; r6 = 0x00073F00
0x00785a  orrs r7,r6
0x00785c  str r7,[r1,#0x4]           ; GPIO3_MODE |= 0x73F00    (pins 8..16)
0x00785e  movs r7,#0xb
0x007860  lsls r7,r7,#0x8
0x007862  str r7,[r4,#0x28]          ; GPIO0_BCLR = 0xB00
0x007864  movs r7,#0x30
0x007866  str r7,[r0,#0x28]          ; GPIO1_BCLR = 0x30
0x007868  movs r7,#0xf
0x00786a  lsls r7,r7,#0xa
0x00786c  str r7,[r2,#0x28]          ; GPIO2_BCLR = 0x3C00
0x00786e  str r6,[r1,#0x28]          ; GPIO3_BCLR = 0x73F00
0x007870  ldr r6,[r4,#0x4]
0x007872  movs r7,#0xff
0x007874  orrs r6,r7
0x007876  str r6,[r4,#0x4]           ; GPIO0_MODE |= 0xFF       (pins 0..7)
0x007878  ldr r7,[r0,#0x4]
0x00787a  ldr r6,[0x000078d4]        ; r6 = 0x00040001
0x00787c  orrs r7,r6
0x00787e  str r7,[r0,#0x4]           ; GPIO1_MODE |= 0x40001    (pins 0,18)
0x007880  ldr r7,[r2,#0x4]
0x007882  ldr r6,[0x000078d8]        ; r6 = 0x000043FF
0x007884  orrs r7,r6
0x007886  str r7,[r2,#0x4]           ; GPIO2_MODE |= 0x43FF     (pins 0..9,14)
0x007888  ldr r7,[r1,#0x4]
0x00788a  str r7,[r1,#0x4]           ; GPIO3_MODE unchanged
0x00788c  movs r7,#0xff
0x00788e  str r7,[r4,#0x24]          ; GPIO0_BSET = 0xFF
0x007890  ldr r7,[0x000078d4]
0x007892  str r7,[r0,#0x24]          ; GPIO1_BSET = 0x40001
0x007894  str r6,[r2,#0x24]          ; GPIO2_BSET = 0x43FF
0x007896  str r3,[r1,#0x24]          ; GPIO3_BSET = 0
0x007898  bl 0x0000aeb6
0x00789c  ldr r1,[r4,#0x4]
0x00789e  asrs r0,r5,#0x1            ; 0x8000>>1 = 0x4000
0x0078a0  orrs r1,r0
0x0078a2  str r1,[r4,#0x4]           ; GPIO0_MODE |= 0x4000
0x0078a4  ldr r1,[r4,#0x24]
0x0078a6  orrs r1,r0
0x0078a8  str r1,[r4,#0x24]          ; GPIO0_BSET |= 0x4000
0x0078aa  ldr r0,[r4,#0x4]
0x0078ac  orrs r0,r5
0x0078ae  str r0,[r4,#0x4]           ; GPIO0_MODE |= 0x8000     (pin 15)
0x0078b0  ldr r0,[r4,#0x24]
0x0078b2  orrs r0,r5
0x0078b4  str r0,[r4,#0x24]          ; GPIO0_BSET |= 0x8000
0x0078b6  pop {r3,r4,r5,r6,r7,pc}
```

> The instruction operands above are Ghidra's. Note the two "surprising" immediates:
> `0x00780E` is `lsls r0,r0,#0xd` on `r0=5` → **`0xA000`**, and `0x00781A` is
> `lsls r1,r1,#0x1d` on `r1=5` → **`0xA0000000`**. Both decode to `0b01`-laden `CFG`/`CFG1`
> field patterns. An earlier draft of this note reported `0x5000` and `0x20000000` for these;
> those came from a buggy hand-rolled decoder (§7 A9) and are **wrong**. The literal-pool chain
> was re-verified directly:
>
> ```
> 0x007802 hw=0x4C2E -> 0x0078BC = 0x40044000     0x007814 hw=0x482A -> 0x0078C0 = 0x40046000
> 0x007804 hw=0x4D2C -> 0x0078B8 = 0x0000FFFF     0x00781E hw=0x4929 -> 0x0078C4 = 0x0000800A
> 0x007824 hw=0x4A28 -> 0x0078C8 = 0x40048000     0x00782C hw=0x4927 -> 0x0078CC = 0x4004A000
> 0x007858 hw=0x4E1D -> 0x0078D0 = 0x00073F00     0x00787A hw=0x4E16 -> 0x0078D4 = 0x00040001
> 0x007882 hw=0x4E15 -> 0x0078D8 = 0x000043FF     0x007890 hw=0x4F10 -> 0x0078D4 = 0x00040001
> ```

### E5 — literal pools (raw bytes)

```
0x0078B8 = 0x0000FFFF    0x0078BC = 0x40044000    0x0078C0 = 0x40046000
0x0078C4 = 0x0000800A    0x0078C8 = 0x40048000    0x0078CC = 0x4004A000
0x0078D0 = 0x00073F00    0x0078D4 = 0x00040001    0x0078D8 = 0x000043FF

0x000F94 = 0x000C0400    0x000F98 = 0x40044000    0x000F9C = 0x200012DC
0x000FA0 = 0x0008C006    0x000FA4 = 0x40046000    0x000FA8 = 0x4004A000
0x000FAC = 0xFFFDFFFD    0x000FB0 = 0x40060000    0x000FB4 = 0xFEFBFFFF
0x000FB8 = 0x200000A8    0x000FBC = 0x200001F3

0x002270 = 0x40046000 (GPIO1)   0x002274 = 0x20000040 (SRAM base)
0x002278 = 0x40044000 (GPIO0)   0x00227C = 0x20001394
0x002280 = 0x200000FC           0x002284 = 0x20000103
0x002288 = 0x200000F4           0x00228C = 0x00002EE0

0x0035E0 = 0x40044000 (GPIO0)   0x0035E4 = 0x00040001
0x0035E8 = 0x40046000 (GPIO1)   0x0035EC = 0x000043FF
0x0035F0 = 0x40048000 (GPIO2)   0x0035F4 = 0x4004A000 (GPIO3)
0x0035F8 = 0x20000040

0x0033D8 = 0x20000040           0x003418 = 0x20001348
0x00341C = 0x2000016E           0x003420 = 0x40044000
0x003424 = 0x40048000           0x003428 = 0x40046000

0x0047E0 = 0x4004A000 (GPIO3)   0x0047E4 = 0x40046000 (GPIO1)
0x0047E8 = 0x40044000 (GPIO0)

0x0051B4 = 0x40046000           0x0051B8 = 0x4004A000
0x0051BC = 0x40048000           0x0051C0 = 0x40044000
0x0051C4 = 0x00073F00           0x0051CC = 0x2000135B
0x0051D0 = 0x20000042

0x011450 = 0x40044000           0x011454 = 0x40046000
0x011458 = 0x40048000           0x01145C = 0x4004A000
0x011460 = 0x00073F00           0x011464 = 0x2000016D
0x011468 = 0x20001782           0x01146C = 0x200018A8
0x011470 = 0x200019CE           0x011768 = 0x40044000
```

### E6 — the debounce function `FUN_000032e0` @ `0x003348` (decompiled)

```c
if (*(char *)(DAT_000033d8 + 2) != '\0') {          /* 0x20000040 + 2 */
  uVar8 = 0;
  do {
    bVar1 = *(byte *)(DAT_00003418 + 0x13 + uVar8); /* prev   : 0x2000135B + i */
    bVar2 = *(byte *)(DAT_00003418 + 0x26 + uVar8); /* raw    : 0x2000136E + i */
    if ((uint)bVar1 == (uint)bVar2) {
      cVar3 = *(char *)(iVar5 + uVar8);             /* count  : 0x20001348 + i */
      if (cVar3 != '\0') {
        *(char *)(iVar5 + uVar8) = cVar3 + '\x01';
        if (2 < (byte)(cVar3 + 1U)) {               /* 3 stable samples */
          *(undefined1 *)(iVar5 + uVar8) = 0;
          iVar7 = DAT_00003418 + 0x39;              /* last   : + 0x39 */
          uVar6 = (uint)(*(byte *)(iVar7 + uVar8) ^ bVar2);
          *(uint *)(iVar4 + 0x38) = uVar6;
          if (uVar6 != 0) {
            *(char *)(iVar4 + 3)   = (char)uVar8;   /* changed index */
            *(uint *)(iVar4 + 0x34) = (uint)bVar2;  /* new state     */
            *(byte *)(iVar7 + uVar8) = bVar2;
            FUN_00003292();
          }
        }
      }
    } else { *(byte *)(DAT_00003418 + 0x26 + uVar8) = bVar1;
             *(undefined1 *)(iVar5 + uVar8) = 1; }
    uVar8 = uVar8 + 1 & 0xff;
  } while (uVar8 < 0x13);                            /* 19 inputs */
  FUN_00002388();
  *(undefined1 *)(iVar4 + 2) = 0;
}
```

`DAT_000033d8 = 0x20000040`, `DAT_00003418 = 0x20001348` (from the pool at `0x0033D8`/`0x003418`).

### E7 — the per-sample reader `FUN_000034ee` @ `0x0034EE` (decompiled)

```c
byte FUN_000034ee(void) {
  byte bVar1;
  FUN_000034c8();  FUN_000034ae();  FUN_00003352();
  FUN_00006f1a(10);
  bVar1 = *DAT_000035e8 << 0x11 < 0;                 /* GPIO1_DATA bit 15 -> b0 */
  if (*DAT_000035e8 << 0x10 < 0) bVar1 |= 2;         /* GPIO1_DATA bit 14 -> b1 */
  if (*DAT_000035e8 << 0x0c < 0) bVar1 |= 4;         /* GPIO1_DATA bit 11 -> b2 */
  if (*DAT_000035f4 << 0x0c < 0) bVar1 |= 8;         /* GPIO3_DATA bit 11 -> b3 */
  if (*DAT_000035e0 << 0x0c < 0) bVar1 |= 0x10;      /* GPIO0_DATA bit 11 -> b4 */
  if (*DAT_000035e0 << 0x0d < 0) bVar1 |= 0x20;      /* GPIO0_DATA bit 10 -> b5 */
  return bVar1;
}
```

`FUN_00005174` @ `0x005174` (decompiled) fills the 19-byte buffer:

```c
iVar1 = DAT_00005198;                 /* 0x2000016D */
*(byte*)(DAT_00005198 + 1) = 0;
iVar2 = DAT_000051cc;                 /* 0x2000135B */
do {
  uVar3 = FUN_000034ee();
  uVar4 = (uint)*(byte*)(iVar1 + 1);
  *(byte*)(iVar2 + uVar4) = uVar3;
  uVar4 = uVar4 + 1;
  *(char*)(iVar1 + 1) = (char)uVar4;
} while ((uVar4 & 0xff) < 0x13);      /* 19 */
*DAT_000051d0 = 1;                    /* 0x20000042 */
```

### E8 — the 3-wire bit-bang `FUN_000043e4` @ `0x0043E4` (disassembled)

```
0x0043e4  push {r4,r5,r6,lr}
0x0043e6  movs r1,#0x0                ; result = 0
0x0043e8  movs r5,#0x40               ; mask 0x40 -> GPIO3 pin 6
0x0043ea  ldr r2,[0x000047e0]         ; r2 = 0x4004A000 GPIO3
0x0043ec  ldr r6,[0x000047e4]         ; r6 = 0x40046000 GPIO1
0x0043ee  movs r4,#0x20               ; mask 0x20 -> GPIO3 pin 5
0x0043f0  lsls r3,r0,#0x18            ; test bit 7 of arg (= MSB of the byte)
0x0043f2  bpl 0x000043fc
0x0043f4  ldr r3,[r2,#0x24]           ; GPIO3_BSET
0x0043f6  orrs r3,r5
0x0043f8  str r3,[r2,#0x24]           ;   data out = 1
0x0043fa  b 0x00004402
0x0043fc  ldr r3,[r2,#0x28]           ; GPIO3_BCLR
0x0043fe  orrs r3,r5
0x004400  str r3,[r2,#0x28]           ;   data out = 0
0x004402  lsls r0,r0,#0x19
0x004404  lsrs r3,r0,#0x18            ; arg <<= 1
0x004406  ldr r0,[r2,#0x24]           ; GPIO3_BSET
0x004408  orrs r0,r4
0x00440a  str r0,[r2,#0x24]           ; clock = 1
0x00440c  ldr r0,[r6,#0x0]            ; GPIO1_DATA
0x00440e  lsls r0,r0,#0x16
0x004410  lsrs r0,r0,#0x1f            ; bit 22 = data in
0x004412  orrs r0,r3                  ; result = (result<<1) | bit
0x004414  ldr r3,[r2,#0x28]           ; GPIO3_BCLR
0x004416  orrs r3,r4
0x004418  str r3,[r2,#0x28]           ; clock = 0
0x00441a  adds r1,r1,#0x1
0x00441c  uxtb r1,r1
0x00441e  cmp r1,#0x8
0x004420  bcc 0x000043f0
0x004422  pop {r4,r5,r6,pc}
```

### E9 — tooling written for this analysis (read-only, in-tree)

| File | Purpose |
|---|---|
| [thumbdis.py](research/tools/thumbdis.py) | Thumb-1 / ARMv6-M decoder + linear sweep |
| [funcdump.py](research/tools/funcdump.py) | Disassemble a function from a raw image with pool resolution |
| [gpioscan.py](research/tools/gpioscan.py) | Const-propagating scanner that resolves `GPIOx + offset` accesses |

Reproduce the key numbers:

```powershell
cd <REPO_ROOT>\research\tools
$img = '<REPO_ROOT>\keyboard\stock\S98Pro_SN32F290_stock.bin'
python gpioscan.py $img all > gpio_accesses.txt      # every resolved GPIO touch
python funcdump.py $img 0x1E80                        # the 3-bit mode reader
```

### E10 — sources cross-referenced

| Claim | Source |
|---|---|
| GPIO base addresses | `research/SN32F290.patched.svd`; `research/ChibiOS-Contrib/os/common/ext/SONiX/SN32F2xx/SN32F290.h:4786`; `s98rs/pac/src/lib.rs` |
| Register offsets and names | SVD, parsed; `SN32F290.h:638-959` |
| `MODE` = direction, `CFG` pull/schmitt semantics | `research/ChibiOS-Contrib/os/hal/ports/SN32/LLD/SN32F2xx/GPIO/hal_pal_lld.c:202-251` |
| IRQ numbers for GPIO0–3 | SVD `<interrupt>` blocks: IRQ28 P3, 29 P2, 30 P1, 31 P0 |
| NVIC register addresses used by the ISRs (`0xE000E280` ICPR, `0xE000E180` ISER) | ARMv6-M architectural; `0x0051C4`-style pool literals at `0x000F98`/`0x000FA8` |

---

*Analysis performed read-only against the stock image. No device was connected, nothing was
flashed, and no files outside this note and `research/tools/` were created.*
