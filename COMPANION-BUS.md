# The Companion Controller Bus

Reverse-engineering note on `keyboard/stock/S98Pro_SN32F290_stock.bin` (256 KB, SN32F290,
Cortex-M0), Ghidra program `S98Pro_SN32F290_stock.bin`.

**Read-only analysis. No hardware touched, nothing flashed, nothing outside this file written.**

All addresses are flash/file offsets (image is loaded flat at `0x00000000`, so Ghidra address ==
file offset).

---

## Summary

### There is no companion controller. It is a 6 × 19 GPIO key matrix — with one output pin per column.

This contradicts the premise in [MATRIX-PINS.md](MATRIX-PINS.md) §1.2 and I am saying so plainly,
because the previous conclusion is wrong and would have sent the replacement firmware down a dead
end. The "19 abstract slots from an off-chip companion" are **19 GPIO pins that the MCU drives
one-at-a-time**, and the "six bits packed per sample" are **six GPIO pins the MCU reads as matrix
rows**.

| Fact | Value | Where |
|---|---|---|
| Bus shape | **one-hot column drive + 6 parallel row reads**, 19 columns per cycle | `FUN_00003352` @ `0x003352`, `FUN_000034c8` @ `0x0034C8`, `FUN_000034ee` @ `0x0034EE` |
| Columns | **19** output pins: `GPIO0.0–7`, `GPIO2.0–9`, `GPIO2.14` (2 more defined, unused: `GPIO1.0`, `GPIO1.18`) | select switch, table below |
| Rows | **6** input pins: `GPIO1.14`, `GPIO1.15`, `GPIO1.19`, `GPIO3.19`, `GPIO0.19`, `GPIO0.18` | `FUN_000034ee` @ `0x003504`–`0x003542` |
| Key positions | 6 × 19 = **114**; **97** of them carry a HID code | keymap @ `0x00011BE4` |
| Select polarity | exactly one column driven **HIGH**, the other 20 driven **LOW** | `FUN_000034AE` (BCLR all) then `FUN_00003352` (BSET one) |
| Row polarity | key pressed ⇒ row reads **1** | [I], §2.5 — forced by the column polarity |
| Settle delay before reading | `FUN_00006f1a(10)` ≈ **300 core cycles ≈ 6 µs @48 MHz** | `0x0034FE`, `FUN_00006F1A` @ `0x006F1A` |
| Where slots become keycodes | **`keycode = *(u8*)(0x00011BE4 + slot*8 + bit)`** | `FUN_00003292` @ `0x0032B8`–`0x0032BC` |
| Debounce | 3-consecutive-equal-samples counter per slot, then XOR edge detect | `FUN_000032E0` @ `0x0032E0` |
| 3-wire bit-bang | **status display** (LCD/OLED), *not* the key path | §5 |

### Why the earlier analysis concluded "no matrix" — and why that was wrong

Two reasons, both now resolved:

1. **The scan is not a loop.** It is a 22-way `switch` compiled to `__ARM_common_switch8`
   (`0x003366` → helper at `0x008E78`), whose 22 case bodies each set **one** GPIO pin. A
   loop-shaped search for "row drive / column sense" finds nothing.
2. **The pins are made outputs *inside the per-sample reader*, not in the init function.** All four
   ports really are `MODE = 0` after `FUN_00007800` @ `0x007800`. They become outputs at
   `FUN_000034C8` @ `0x0034C8`, which runs on **every sample** and which the earlier GPIO sweep
   never associated with the key path.

The MODE masks in `FUN_000034C8` are exactly the union of the BSET masks in the `FUN_00003352`
switch — `0x000000FF` / `0x00040001` / `0x000043FF` = 8 + 2 + 11 = **21 pins** = every case body
except the default. That correspondence is the proof that the 21 "address" pins and the 19 "slots"
are the same thing.

### Confidence

| Claim | Confidence |
|---|---|
| 19 one-hot column pins, identities and slot order | **Verified** (disassembly + literal pool) |
| 6 row pins, identities and bit order | **Verified** (disassembly) |
| 97 keys mapped by `0x00011BE4[slot*8+bit]` | **Verified** (disassembly + table dump) |
| Pressed = row HIGH (not LOW) | **Inference**, argued in §2.5; measure before trusting |
| Direct matrix vs. an intermediate buffer/diode network on the PCB | **Unknown** — see §7 (U1) |

---

## 1. Bus shape

**A 6-row × 19-column matrix, scanned one column at a time, with every unselected column actively
driven LOW.** Not a shift register, not serial, not a multiplexed address/data scheme, and not an
off-chip expander.

One full sample (`FUN_000034ee` @ `0x0034EE`, verbatim order):

| # | Action | Address | Evidence |
|---|---|---|---|
| 1 | `GPIO0_MODE \|= 0x000000FF` | `0x0034C8` → `0x0034D0` | columns GPIO0.0–7 → outputs |
| 2 | `GPIO1_MODE \|= 0x00040001` | `0x0034D2` → `0x0034DA` | columns GPIO1.0, GPIO1.18 → outputs |
| 3 | `GPIO2_MODE \|= 0x000043FF` | `0x0034DC` → `0x0034E4` | columns GPIO2.0–9, GPIO2.14 → outputs |
| 4 | `GPIO3_MODE` read then written back unchanged | `0x0034E6` → `0x0034EA` | no-op; vendor idiom |
| 5 | `GPIO0_BCLR = 0x000000FF` | `0x0034AE` → `0x0034B2` | **all columns LOW** |
| 6 | `GPIO1_BCLR = 0x00040001` | `0x0034B6` | all columns LOW |
| 7 | `GPIO2_BCLR = 0x000043FF` | `0x0034BE` | all columns LOW |
| 8 | `GPIO3_BCLR = 0` | `0x0034C2` | no-op |
| 9 | **one** column `BSET` per the slot index | `0x003352` switch table | **exactly one column HIGH** |
| 10 | `FUN_00006f1a(10)` — settle delay | `0x0034FE` | ≈300 core cycles |
| 11 | read 6 row pins into one byte | `0x003504`–`0x003542` | see §2.2 |
| 12 | next index, repeat 19× | `FUN_00005174` @ `0x005174` / `FUN_000050BE` @ `0x0050BE` | |

Steps 1–4 are idempotent (`MODE` is OR-ed, never cleared), so a replacement firmware needs them
once at init, not per sample. Steps 5–8 are not optional: they are what makes the scan
ghost-free — a pressed key on an *unselected* column is shorted to a driven-LOW line, so it can
never raise its row. **This is the whole design: one output per column buys diodes-free NKRO.**

The `BSET`/`BCLR` writes in the stock code are read-modify-write of a write-only register
(`ldr r0,[r1,#0x24] / orrs r0,rX / str r0,[r1,#0x24]`), e.g. `0x003382`. **Do not copy that** —
issue a plain write of the mask.

### 1.1 Scan pacing

Two paths exist; either can be reimplemented.

| Path | Driver | Behaviour |
|---|---|---|
| **Interrupt-driven** (normal) | `FUN_000050BE` @ `0x0050BE` = **IRQ19 / CT16B3** handler | one column per timer interrupt; reads `CT16B3_RIS` (`0x400060A8`) bit 0, clears `CT16B3_IC` (`0x400060AC`) = 1, takes one sample, stores it, advances the index; after the 19th sample resets the index to 0 and sets the "scan ready" flag `*(u8*)0x20000042 = 1` |
| **Software burst** | `FUN_00005174` @ `0x005174` | loops the index 0…18 calling `FUN_000034ee` 19× back-to-back, then sets `*(u8*)0x20000042 = 1`. Called from `FUN_00000444` @ `0x000444` (when `*(u8*)0x00000604 == 5`) and from `FUN_000061E4` @ `0x0061E4` (a fallback poll loop with `FUN_00006f1a(500)` between passes) |

`CT16B3` prescale is set to `0x2F` (= /48) by `FUN_00007954` @ `0x007954` (`DAT_00007A98[2] = 0x2F`
where `DAT_00007A98 = 0x40006000`). The match value that sets the actual tick period is written
through the `0x5A……` LED-PWM magic in the same function and **I could not isolate it** (§7 U2).
A full scan in the software path costs 19 × ≈340 cycles ≈ **135 µs @48 MHz**, so >2 kHz is
achievable; latency only needs to beat the 3-sample debounce (§4).

---

## 2. Pin roles and the scan sequence

### 2.1 Columns (driven) — slot order

Slot index is the order `FUN_00005174` writes into the sample array
(`0x2000016E` = index, `0x2000135B + index` = sample).

| slot | port.pin | BSET mask | case body | | slot | port.pin | BSET mask | case body |
|---|---|---|---|---|---|---|---|---|
| 0 | **GPIO0.4** | `0x00000010` | `0x003382` | | 10 | **GPIO2.6** | `0x00000040` | `0x00343E` |
| 1 | **GPIO0.5** | `0x00000020` | `0x00338A` | | 11 | **GPIO2.7** | `0x00000080` | `0x003448` |
| 2 | **GPIO2.0** | `0x00000001` | `0x003394` | | 12 | **GPIO2.14** | `0x00004000` | `0x003452` |
| 3 | **GPIO2.1** | `0x00000002` | `0x00339C` | | 13 | **GPIO2.8** | `0x00000100` | `0x00345E` |
| 4 | **GPIO2.2** | `0x00000004` | `0x0033A6` | | 14 | **GPIO2.9** | `0x00000200` | `0x00346A` |
| 5 | **GPIO2.3** | `0x00000008` | `0x0033B0` | | 15 | **GPIO0.0** | `0x00000001` | `0x003476` |
| 6 | **GPIO0.6** | `0x00000040` | `0x0033B8` | | 16 | **GPIO0.1** | `0x00000002` | `0x00347E` |
| 7 | **GPIO0.7** | `0x00000080` | `0x0033C2` | | 17 | **GPIO0.2** | `0x00000004` | `0x003488` |
| 8 | **GPIO2.4** | `0x00000010` | `0x00342C` | | 18 | **GPIO0.3** | `0x00000008` | `0x003492` |
| 9 | **GPIO2.5** | `0x00000020` | `0x003434` | | 19 | *GPIO1.0* | `0x00000001` | `0x00349A` (not scanned) |
| | | | | | 20 | *GPIO1.18* | `0x00040000` | `0x0034A2` (not scanned) |
| | | | | | 21 | — (default) | — | `0x003388` = immediate return |

Switch dispatch: `0x003366` = `bl __ARM_common_switch8`; count byte `[0x336A] = 0x15` (21);
byte table `0x336B…0x3380`; case address = `0x336A + 2*table[i]`. Helper `0x008E78` verified:
`ldrb r5,[lr-1]; ldrb r3,[base+r5]; lsls r3,#1; add r3,base,r3; bx r3`.

The `__ARM_common_switch8` call is *invisible* to Ghidra's decompiler at `0x003352`, which
renders the whole routine as a garbage indirect call. That is why this bus stayed hidden.

Note the order is **not** monotonic in pin number (`…GPIO2.9, GPIO2.14(12), GPIO2.8, GPIO2.9…`).
It is the order each case appears in the table, i.e. it is the vendor's chosen scan order and it
does not matter for correctness — but the slot number **is** the keymap row index, so it must be
preserved exactly.

### 2.2 Rows (sensed) — bit order

`FUN_000034EE` @ `0x0034EE`, disassembled. Each test is `lsls rX,rX,#n` / `bpl`, which tests
**bit `31-n`** of the 32-bit port `DATA` register:

| bit in sample byte | instruction | shift | `31 - shift` | **pin** |
|---|---|---|---|---|
| b0 | `0x003506` `lsls r1,r1,#0x11` | 17 | 14 | **GPIO1.14** |
| b1 | `0x00350E` `lsls r1,r1,#0x10` | 16 | 15 | **GPIO1.15** |
| b2 | `0x003518` `lsls r0,r0,#0x0c` | 12 | 19 | **GPIO1.19** |
| b3 | `0x003522` `lsls r0,r0,#0x0c` | 12 | 19 | **GPIO3.19** |
| b4 | `0x00352E` `lsls r1,r1,#0x0c` | 12 | 19 | **GPIO0.19** |
| b5 | `0x00353A` `lsls r0,r0,#0x0d` | 13 | 18 | **GPIO0.18** |
| b6, b7 | — | — | — | **never set** (always 0) |

> **Correction to [MATRIX-PINS.md](MATRIX-PINS.md) §1.2 / E7.** That note lists the six bits as
> `GPIO1.15, GPIO1.14, GPIO1.11, GPIO3.11, GPIO0.11, GPIO0.10`. It read `x << n` as "bit n",
> but it is "bit 31−n". The correct pins are the table above. Every pin number derived from this
> idiom in the earlier note (rows, the 3-bit mode switch in `FUN_00001ef0`, the ISR pin lists)
> should be re-derived.

The stock code re-reads `GPIO1_DATA` three times rather than caching it (`0x003504`, `0x00350E`,
`0x003518`) — an artifact of the compiler, not protocol. One read per port is equivalent.

### 2.3 What is *not* on this bus

| Pin | Role | Evidence |
|---|---|---|
| `GPIO0.14` | active-low strobe for the 3-wire display link (high at idle) | `FUN_00004424` @ `0x004424`, `FUN_00007800` @ `0x0078A4` (`MODE \|= 0x4000`, `BSET = 0x4000`) |
| `GPIO0.15` | second control line of the same link | `FUN_0000482E` @ `0x00482E` (BCLR `0x8000`), `FUN_00004466` @ `0x004466` (BSET `0x8000`) |
| `GPIO3.5` | 3-wire clock | `FUN_000043E4` @ `0x0043F4`–`0x004418` |
| `GPIO3.6` | 3-wire data out | `FUN_000043E4` |
| `GPIO1.22` | 3-wire data in | `FUN_000043E4` @ `0x00440C` |
| GPIO1.1, GPIO1.2, GPIO0.10… | mode switch / status inputs | outside this bus, §7 U3 |

`GPIO0.19` and `GPIO1.14`/`GPIO1.15` and `GPIO3.19` (four of the six **row** pins) are also the
ISR-clear masks of the four GPIO interrupt handlers — `GPIO0_IC = 0x000C0400`,
`GPIO1_IC = 0x0008C006`, `GPIO3_IC = 0x00080000`. They double as keypress wake-up sources.
Left as-is in a replacement firmware; the row lines will generate spurious IRQs while scanning
because they toggle on every column, so **disable those GPIO interrupts or mask/ignore them.**

### 2.4 "How can a signal come out of an input pin?"

It cannot. The direction is changed first, every sample, at `FUN_000034C8`:

```c
*(volatile u32*)(GPIO0 + 0x04) |= 0x000000FF;   /* 0x0034D0 */
*(volatile u32*)(GPIO1 + 0x04) |= 0x00040001;   /* 0x0034DA */
*(volatile u32*)(GPIO2 + 0x04) |= 0x000043FF;   /* 0x0034E4 */
*(volatile u32*)(GPIO3 + 0x04)  = *(volatile u32*)(GPIO3 + 0x04);  /* 0x0034EA, no-op */
```

`MODE` bit = 1 ⇒ output (SVD `MODE0…MODE19`). Because `MODE` is only ever OR-ed, the first sample
turns all 21 columns into outputs and they stay outputs. This is the fact the previous sweep
missed.

### 2.5 Row polarity — pressed is HIGH

The code gives no explicit polarity, so this is derived, not read:

1. Exactly one column is HIGH and the other 20 are driven LOW (§1).
2. If rows idled HIGH (pull-up), a key would short its row to a **LOW** column — and since 20 of 20
   unselected columns are LOW, *any* key in a row would pull that row low. The row state would then
   be "some key in this row is pressed", independent of which column is selected. That cannot
   produce a working matrix, so rows cannot idle HIGH.
3. Therefore rows idle **LOW** (external pull-downs) and a key at (row r, selected column c) pulls
   row r **HIGH**. Pressed ⇒ bit = 1.
4. Consistent with the software: `FUN_00003292` @ `0x0032AE`–`0x0032B6` sets
   `pressed = 1` when the new state bit is 1, with **no inversion anywhere** in the pipeline. If
   the hardware were active-low, every key would register inverted and the keyboard could not type.
5. Consistent with the vendor's own pull-up handling: `GPIO1_CFG = 0x20000000` clears the pull-up
   on `GPIO1.14` (`CFG` field 14 = bits 28–29 = `0b10`), and `GPIO0_CFG1 = 0xA000` /
   `GPIO3_CFG1 = 0x8000` are **botched attempts** to clear pull-ups on the pins 18/19 fields
   (both write bit offsets ≥ 12, i.e. pins ≥ 22, which do not exist — see MATRIX-PINS §7 A1).
   The clear intent was "no pull-up on the row pins". Two row pins (`GPIO1.15`, `GPIO1.19`) keep
   reset/default pull-ups in the stock image; a strong external pull-down wins over the internal
   ~50 kΩ, so it still works.

**Verify in 60 seconds on the bench:** with nothing pressed, all 19 samples must read `0x00`.
Press one key; exactly one slot must show a single set bit. If instead you see all-`0x3F` at idle
and a bit *clearing* on a press, invert `row_bits()` — and re-measure the pull-up configuration.

Set `CFG`/`CFG1` fields of the six row pins to **`0b10`** (pull-up off, schmitt on) in your own
firmware; that is what the vendor intended and it removes any divider concern.

---

## 3. What the 19 values mean, and where they become keycodes

### 3.1 The values

Each of the 19 sample bytes uses **6 bits**: bit *b* = row *b* = the pin in §2.2. Bits 6–7 are
never set. So a "slot value" is a bitfield of the 6 keys on that column, and 19 × 6 = **114 key
positions**, of which **97** are populated in the keymap — matching a 98-key board (97 matrix keys
+ the rotary encoder, which is not on this matrix; §7 U4).

### 3.2 THE MISSING LINK — found

**`FUN_00003292` @ `0x003292` is where a bus slot becomes a HID usage code.** Verbatim:

```
0x32A0  ldrb r0,[r4,#0x3]      ; r4 = 0x20000040; [r4+3] = changed slot index (0..18)
0x32A2  ldrb r1,[r4,#0x4]      ; [r4+4] = bit counter, 0..7
0x32A4  lsls r0,r0,#0x3        ; slot * 8
0x32A6  adds r0,r0,r1          ; + bit
0x32A8  uxtb r0,r0
0x32AA  strb r0,[r4,#0x5]      ; 0x20000045 = flat index
0x32AC  strb r5,[r4,#0x9]      ; r5 = 0 -> not pressed
0x32AE  ldrh r1,[r4,#0x34]     ; 0x20000074 = settled state byte for this slot
0x32B0  lsls r1,r1,#0x1f       ; test bit 0
0x32B2  beq  0x32B8
0x32B4  movs r1,#0x1
0x32B6  strb r1,[r4,#0x9]      ; 0x20000049 = 1 -> pressed
0x32B8  ldr  r1,[0x00003414]   ; r1 = 0x00011BE4   <-- KEYMAP TABLE
0x32BA  ldrb r0,[r1,r0]        ; r0 = TABLE[slot*8 + bit]
0x32BC  strb r0,[r4,#0x6]      ; 0x20000046 = HID usage code
0x32BE  bl   0x00003004         ; apply to the report state
0x32C2  bl   0x00002d16         ; build/emit
0x32C6..  shift state byte and changed mask right; bit++; loop while bit < 8
```

The literal was resolved from the pool at `0x003414 = 0x00011BE4` (read directly from the image:
`e4 1b 01 00`). **`0x00011BE4` is the keycode table: 160 bytes = 20 slots × 8 bits, indexed
`slot*8 + bit`, value = USB HID usage ID.** `FUN_00003004` @ `0x003004` and the matrices at
`0x000120F4` / `0x00011FB4` are downstream bookkeeping (per-key LED index, key numbering), not the
key path.

### 3.3 The table, fully decoded

`0x00011BE4 + slot*8 + row` → HID usage (0 = no key at that position). HID names filled in.

| slot | column pin | r0 | r1 | r2 | r3 | r4 | r5 |
|---|---|---|---|---|---|---|---|
| 0 | GPIO0.4 | 41 `Esc` | 53 `` ` `` | 43 `Tab` | 57 `Caps` | 225 `LShift` | 224 `LCtrl` |
| 1 | GPIO0.5 | 58 `F1` | 30 `1` | 20 `Q` | 4 `A` | 100 `F13` | 227 `LGUI` |
| 2 | GPIO2.0 | 59 `F2` | 31 `2` | 26 `W` | 22 `S` | 29 `Z` | 226 `LAlt` |
| 3 | GPIO2.1 | 60 `F3` | 32 `3` | 8 `E` | 7 `D` | 27 `X` | — |
| 4 | GPIO2.2 | 61 `F4` | 33 `4` | 21 `R` | 9 `F` | 6 `C` | — |
| 5 | GPIO2.3 | 62 `F5` | 34 `5` | 23 `T` | 10 `G` | 25 `V` | — |
| 6 | GPIO0.6 | 63 `F6` | 35 `6` | 28 `Y` | 11 `H` | 5 `B` | 44 `Space` |
| 7 | GPIO0.7 | 64 `F7` | 36 `7` | 24 `U` | 13 `J` | 17 `N` | — |
| 8 | GPIO2.4 | 65 `F8` | 37 `8` | 12 `I` | 14 `K` | 16 `M` | — |
| 9 | GPIO2.5 | 66 `F9` | 38 `9` | 18 `O` | 15 `L` | 54 `,` | — |
| 10 | GPIO2.6 | 67 `F10` | 39 `0` | 19 `P` | 51 `;` | 55 `.` | 230 `RAlt` |
| 11 | GPIO2.7 | 68 `F11` | 45 `-` | 47 `[` | 52 `'` | 56 `/` | 175 `0xAF` |
| 12 | GPIO2.14 | 69 `F12` | 46 `=` | 48 `]` | 50 `0x32` | 135 `0x87` | 228 `RCtrl` |
| 13 | GPIO2.8 | — | 42 `BkSp` | 49 `\` | 40 `Enter` | 229 `RShift` | 80 `Left` |
| 14 | GPIO2.9 | — | — | — | — | 82 `Up` | 81 `Down` |
| 15 | GPIO0.0 | — | 76 `Del` | 95 `KP7` | 92 `KP4` | 89 `KP1` | 79 `Right` |
| 16 | GPIO0.1 | — | 84 `KP/` | 96 `KP8` | 93 `KP5` | 90 `KP2` | 98 `KP0` |
| 17 | GPIO0.2 | — | 85 `KP*` | 97 `KP9` | 94 `KP6` | 91 `KP3` | 99 `KP.` |
| 18 | GPIO0.3 | — | 86 `KP-` | 87 `KP+` | — | 88 `KP Enter` | — |
| 19 | *(not scanned)* | all zero | | | | | |
| 20 | *(not scanned)* | all zero | | | | | |

Sanity check that makes this table self-evident: **slot 0 reads `Esc, \`, Tab, Caps, LShift,
LCtrl`** — the six keys of the leftmost physical column of the board, in top-to-bottom order.

Non-standard codes: `100 = 0x64 = F13`, `175 = 0xAF` (undefined in HID 1.11), `135 = 0x87 =
Keyboard International1 (Ro)`, `50 = 0x32 = Keyboard Non-US #~`. Physical identity of these four
positions is **unknown** (§7 U4) — they are the best candidates for the knob / mode / FN-reassign
keys, but the table is what the firmware consumes.

**97 non-zero entries in slots 0–18** (counted mechanically). The board has 98 keys + 1 rotary
encoder, so one key or the encoder is read somewhere else.

---

## 4. Debounce and edge logic

`FUN_000032E0` @ `0x0032E0`, disassembled (`r5 = 0x20000040`, `r6 = 0x20001348`). Four parallel
19-byte arrays, all offsets from the single literal `0x20001348` at pool `0x003418`:

| Array | Address | Offset from `0x20001348` | Role |
|---|---|---|---|
| A — current sample | `0x2000135B` | `+0x13` | written by the scanner (`FUN_00005174` / `FUN_000050BE`) |
| B — reference / last raw | `0x2000136E` | `+0x26` | tracks A; updated the instant A differs |
| C — stability counter | `0x20001348` | `+0x00` | per-slot count of consecutive unstables |
| D — last accepted state | `0x20001381` | `+0x39` | XOR source for edge detect |

```
loop i = 0..18:
    r1 = A[i]                     ; 0x0032F6  ldrb r1,[r0+0x13+i]
    r0 = B[i]                     ; 0x0032FA  ldrb r0,[r2+0x13+i]
    if (r1 != r0) {               ; 0x0032FC
        B[i] = r1                 ; 0x003300  adopt the new sample immediately
        C[i] = 1                  ; 0x003304
        continue
    }
    if (C[i] == 0) continue       ; 0x00330A  already settled, nothing to do
    C[i]++                        ; 0x00330E-0x003312
    if (C[i] < 3) continue        ; 0x003314
    C[i] = 0                      ; 0x003318
    changed = D[i] ^ B[i]         ; 0x00331E-0x003320
    *(u32*)0x20000078 = changed   ; 0x003322
    if (changed != 0) {
        *(u8*)0x20000043 = i          ; 0x003328  slot index of the event
        *(u32*)0x20000074 = B[i]      ; 0x00332A  settled 6-bit state byte
        D[i] = B[i]                   ; 0x00332C
        FUN_00003292()                ; 0x00332E  -> expand to keycodes (§3.2)
    }
after the loop:
    FUN_00002388()                    ; 0x00333A
    *(u8*)0x20000042 = 0              ; 0x00333E  clear "scan ready"
```

Behaviour, precisely:

* **Debounce depth = 3 samples.** A change is adopted into B immediately but does not become an
  *event* until A has equalled B for two more scans (counter 1→2→3, fire at 3). Equivalently: 3
  consecutive identical samples including the one that differed. At the §1.1 burst rate
  (≈135 µs/scan) that is ≈0.4 ms; at the CT16B3 rate it is 3 timer ticks.
* **`C[i] != 0` is required** on the increment path, so a slot that has never changed never fires
  — this is what makes the first-ever scan produce no phantom events.
* **Edge detection is byte-granular, then bit-expanded.** `changed = D[i] ^ B[i]` is a 6-bit mask
  of which of the column's keys changed; `FUN_00003292` then walks all 8 bits of the *state* byte
  and emits one event per set bit in `changed`, each with `pressed = (state & 1)` (§3.2). Press and
  release are therefore the same path with opposite state bits, and simultaneous press+release
  within one column in the same scan is handled correctly (bit by bit).
* The same `FUN_000032E0` also services a second flag at `0x20000060 + 9` (`0x003342`–`0x00334C`)
  and tail-calls `0x002B8C`.

Everything after `FUN_00003292` (report assembly, `FUN_00003004`, `FUN_00002D16`, USB send) is
outside the bus and unchanged by this document's findings.

---

## 5. The 3-wire bit-bang link

`FUN_000043E4` @ `0x0043E4` is an 8-bit MSB-first shift, 4 instructions per bit, no delays:

```
0x43E4  r2 = 0x4004A000 (GPIO3)   r6 = 0x40046000 (GPIO1)
        r5 = 0x40 (GPIO3.6 = data out)   r4 = 0x20 (GPIO3.5 = clock)
per bit (8x):
        data out := bit7 of arg        ; GPIO3_BSET/BCLR |= 0x40
        arg <<= 1
        GPIO3_BSET |= 0x20             ; clock HIGH
        bit = (GPIO1_DATA >> 22) & 1   ; GPIO1.22 = data in  (0x00440C-0x004410)
        result = (result << 1) | bit
        GPIO3_BCLR |= 0x20             ; clock LOW
```

**It is the status display, not the key path.** Evidence:

| Evidence | Where |
|---|---|
| The register interface it drives is only used by display/UI code | callers of `FUN_00004424`: `0x004466`, `0x0044A2`, `0x0045AE`, `0x00464C`, `0x00466C`, `0x00480C`, `0x00482E`, `0x00487A`, `0x0048A8`, `0x0049AE`, `0x004A18`, `0x004D9E`, `0x004DF2` — all in the `0x43xx–0x4Exx` block |
| That block contains the on-screen text | `0x00E420 "USB Cable!"`, `0x00E43C "Success!"`, `0x00E454 "Fail!!!"`, `0x00E408 "USB Mode"`, `0x00E410 "Link"`, `0x00E468 "2.4G"`, `0x00E470 "BT1"`, `0x00E474 "BT2"`, `0x00E478 "BT3"`, `0x00E47C "Linking"`, `0x00E888 "Pairing"` |
| Pointers to those strings are held in the display code's pools | e.g. `0x00E3E4 → 0x00E40B`, `0x00E884 → 0x00E468`, `0x011C48 → 0x00E487`, `0x0162D8 → 0x00E4C5` |
| Nothing in the key path references it | `FUN_000034EE` / `FUN_00003352` / `FUN_000032E0` / `FUN_00003292` / `FUN_00005174` call none of it; the key pipeline never touches `GPIO3.5`, `GPIO3.6` or `GPIO1.22` |

So it is a **serial peripheral with 8-bit register addresses and 8-bit data**, on 5 wires:

| Signal | Pin | Evidence |
|---|---|---|
| STROBE / chip-select, active low | `GPIO0.14` (`MODE\|=0x4000` once at init, high at idle) | `FUN_00004424` @ `0x004424` (low → transfer → high); `FUN_00007800` @ `0x0078A4` |
| CLOCK | `GPIO3.5` | `FUN_000043E4` |
| DATA out (MCU → device) | `GPIO3.6` | `FUN_000043E4` |
| DATA in (device → MCU) | `GPIO1.22` | `FUN_000043E4` @ `0x00440C` |
| second control | `GPIO0.15` | `FUN_0000482E`/`FUN_00004466` BCLR/BSET `0x8000` |

Register traffic actually observed (read register 7 for status, register 0x60 for a version byte,
write 0x27/0x25/0x20, block writes to 0xA0–0xA4 and 0xB0, `0xE1`/`0xE2` ← 0 as a transaction
prefix):

| Function | Register traffic |
|---|---|
| `FUN_00004424` @ `0x004424` | write(addr, data) — the framing primitive |
| `FUN_00004446` @ `0x004446` | read(addr) — sends addr then 0, shifts in |
| `FUN_0000482E` @ `0x00482E` | `0xE2←0`, `0xE1←0`, `read 7`, `0x27 ← value`, `GPIO0.15` low |
| `FUN_00004466` @ `0x004466` | `0xE2←0`, `read 7`, `0x27 ← value`, `GPIO0.15` high, `0x20 ← read(0) \| 1` |
| `FUN_0000487A` @ `0x00487A` | `0xE1←0`, `GPIO0.15` high, `0x20 ← read(0) & ~1` |
| `FUN_000048A8` @ `0x0048A8` | poll `read 7` until `(v & 0x3F) >> 4 != 0` (≤10 tries, `FUN_00006f1a(100)` between), then `0x27←0x30`, `read 0x60`, `0x61←value` |
| `FUN_000049AE` @ `0x0049AE` | same poll pattern then `0x27←0x30`, `FUN_00004466` |
| `FUN_0000480C` @ `0x00480C` | `0x25 ← table[rolling 0..3]` |
| `FUN_00004A18` @ `0x004A18` | builds command packets `{len, seq, 0xA1…0xA4, payload…}` and sends them via `FUN_0000494E` |

Register 7's bits[3:1] are stored as a 3-bit value and bit 5 is tested (`0x00487A` region,
`FUN_000048A8`), which reads like battery/charging status.

**Conclusion:** the 3-wire link is a status/UI device (the small screen that shows
`USB Mode / 2.4G / BT1 / BT2 / BT3 / Linking / Pairing / USB Cable! / Success! / Fail!!!`). It is
**not** the companion controller, **not** the RGB controller, and **not** on the key path. A
replacement firmware can ignore it entirely and still read keys. The exact part number is
**unknown** (§7 U5) and does not need to be known to read keys.

---

## 6. Rust implementation sketch

Everything below is the complete bus protocol. Constants verified against the image; the only
inferred item is the polarity (§2.5) and the settle time (§7 U2).

```rust
// ---- SN32F290 GPIO ---------------------------------------------------------
const GPIO0: usize = 0x4004_4000;
const GPIO1: usize = 0x4004_6000;
const GPIO2: usize = 0x4004_8000;
const GPIO3: usize = 0x4004_A000;
// register offsets (32-bit, from the SVD / PAC)
const DATA: usize = 0x00;
const MODE: usize = 0x04; // 1 = output
const CFG:  usize = 0x08; // 2 bits/pin, pins 0..15: 0b00 pull-up ON, 0b10 off
const CFG1: usize = 0x30; // 2 bits/pin, pins 16..19, same encoding
const BSET: usize = 0x24; // write 1s to drive HIGH
const BCLR: usize = 0x28; // write 1s to drive LOW

#[inline] fn rd(a: usize) -> u32 { unsafe { (a as *const u32).read_volatile() } }
#[inline] fn wr(a: usize, v: u32) { unsafe { (a as *mut u32).write_volatile(v) } }

// ---- Columns: the 19 select lines, in scan order (slot index == keymap row) --
// mask is a BSET mask for that port.  Order MUST match FUN_00003352's switch table.
const COL: [(usize, u32); 19] = [
    (GPIO0, 1 << 4),  (GPIO0, 1 << 5),  (GPIO2, 1 << 0),  (GPIO2, 1 << 1),
    (GPIO2, 1 << 2),  (GPIO2, 1 << 3),  (GPIO0, 1 << 6),  (GPIO0, 1 << 7),
    (GPIO2, 1 << 4),  (GPIO2, 1 << 5),  (GPIO2, 1 << 6),  (GPIO2, 1 << 7),
    (GPIO2, 1 << 14), (GPIO2, 1 << 8),  (GPIO2, 1 << 9),  (GPIO0, 1 << 0),
    (GPIO0, 1 << 1),  (GPIO0, 1 << 2),  (GPIO0, 1 << 3),
];
// Two more pins are declared as outputs by FUN_000034c8 but never selected by the
// 19-step scanner: GPIO1.0 and GPIO1.18.  Leave them as outputs, driven LOW.
const COL_MODE0: u32 = 0x0000_00FF; // GPIO0.0..7
const COL_MODE1: u32 = 0x0004_0001; // GPIO1.0, GPIO1.18
const COL_MODE2: u32 = 0x0000_43FF; // GPIO2.0..9, GPIO2.14

/// One-time bus init.  Sets the 21 columns as outputs, all driven low, and turns
/// OFF the internal pull-ups on the six row pins (CFG field 0b10 = pull-up off).
pub fn bus_init() {
    // columns -> outputs (never cleared again; FUN_000034c8 only ever ORs)
    wr(GPIO0 + MODE, rd(GPIO0 + MODE) | COL_MODE0);
    wr(GPIO1 + MODE, rd(GPIO1 + MODE) | COL_MODE1);
    wr(GPIO2 + MODE, rd(GPIO2 + MODE) | COL_MODE2);

    // rows: pull-up OFF.  Field for pin n is 2 bits at 2*(n % 16) in CFG (n<=15)
    // or CFG1 (n>=16, offset 2*(n-16)).
    //   rows GPIO1.14, GPIO1.15 : CFG  bits 28..31
    //   rows GPIO1.19           : CFG1 bits 6..7
    //   rows GPIO0.19           : CFG1 bits 6..7
    //   rows GPIO0.18           : CFG1 bits 4..5
    //   row  GPIO3.19           : CFG1 bits 6..7
    const OFF14_15: u32 = 0b10 << 28 | 0b10 << 30;
    const OFF_CFG1_18_19: u32 = 0b10 << 4 | 0b10 << 6;
    wr(GPIO1 + CFG,  rd(GPIO1 + CFG)  & !OFF14_15 | OFF14_15);
    wr(GPIO1 + CFG1, rd(GPIO1 + CFG1) & !OFF_CFG1_18_19 | OFF_CFG1_18_19);
    wr(GPIO0 + CFG1, rd(GPIO0 + CFG1) & !OFF_CFG1_18_19 | OFF_CFG1_18_19);
    wr(GPIO3 + CFG1, rd(GPIO3 + CFG1) & !OFF_CFG1_18_19 | OFF_CFG1_18_19);

    deselect_all();
}

#[inline] fn deselect_all() {
    wr(GPIO0 + BCLR, COL_MODE0); // plain write: BSET/BCLR are write-1-to-act,
    wr(GPIO1 + BCLR, COL_MODE1); // do NOT read-modify-write like the stock code does
    wr(GPIO2 + BCLR, COL_MODE2);
    wr(GPIO3 + BCLR, 0);
}

/// The 6 row bits.  Bit order is the stock firmware's (row 0 = sample bit 0).
#[inline] fn row_bits() -> u8 {
    let g0 = rd(GPIO0 + DATA);
    let g1 = rd(GPIO1 + DATA);
    let g3 = rd(GPIO3 + DATA);
    (((g1 >> 14) & 1) as u8) << 0   // row 0 : GPIO1.14
  | (((g1 >> 15) & 1) as u8) << 1   // row 1 : GPIO1.15
  | (((g1 >> 19) & 1) as u8) << 2   // row 2 : GPIO1.19
  | (((g3 >> 19) & 1) as u8) << 3   // row 3 : GPIO3.19
  | (((g0 >> 19) & 1) as u8) << 4   // row 4 : GPIO0.19
  | (((g0 >> 18) & 1) as u8) << 5   // row 5 : GPIO0.18
}

/// Settle delay.  The stock firmware calls FUN_00006f1a(10):
///   r1 = (10 >> 1) - 1 = 4 ; body() runs 5x
///   body() = FUN_00006f02 = `movs r0,#13` + a 4-instruction loop = 13*4 = 52 cycles
/// => ~260 cycles of inner loop, ~300 with call overhead.
/// At 48 MHz that is ~6.3 us.  Cortex-M0: 1 cycle per instruction (no branch to a
/// taken target is 3, so the real figure is a little higher).
#[inline] fn settle() {
    // Cortex-M0 has no DWT cycle counter; a coarse NOP/loop burn is what the
    // vendor does.  300 iterations of a 1-cycle loop is a safe over-estimate.
    for _ in 0..300 { core::hint::spin_loop(); }
    // If cortex-m DWT CYCCNT is available, prefer:
    //   let t = DWT.cyccnt(); while DWT.cyccnt().wrapping_sub(t) < 400 {}
}

/// One full scan: 19 samples, each the 6-bit row state of one column.
/// ~19 * (300 + ~40) cycles ~= 6.5k cycles ~= 135 us @ 48 MHz.
pub fn scan(out: &mut [u8; 19]) {
    for (i, &(port, mask)) in COL.iter().enumerate() {
        deselect_all();          // all 21 columns LOW
        wr(port + BSET, mask);   // exactly one column HIGH
        settle();                // FUN_00006f1a(10)
        out[i] = row_bits();     // FUN_000034EE's 6 bit tests
    }
    deselect_all();              // leave the bus idle-low between scans
}

// ---- Debounce, exactly like FUN_000032e0 -----------------------------------
pub struct Debouncer {
    cur: [u8; 19],   // 0x2000135B
    r#ref: [u8; 19], // 0x2000136E
    count: [u8; 19], // 0x20001348
    last: [u8; 19],  // 0x20001381
}

impl Debouncer {
    /// Returns (slot, settled_state_byte) for each accepted change.
    pub fn update(&mut self, sample: &[u8; 19], on_event: &mut impl FnMut(u8, u8)) {
        self.cur.copy_from_slice(sample);
        for i in 0..19 {
            let a = self.cur[i];
            let b = self.r#ref[i];
            if a != b {
                self.r#ref[i] = a;   // adopt immediately
                self.count[i] = 1;   // and restart the stability counter
                continue;
            }
            if self.count[i] == 0 { continue; }      // already settled
            self.count[i] = self.count[i].wrapping_add(1);
            if self.count[i] < 3 { continue; }       // 3 equal samples required
            self.count[i] = 0;
            let changed = self.last[i] ^ b;
            if changed != 0 {
                self.last[i] = b;
                on_event(i as u8, b);                // FUN_00003292
            }
        }
    }
}

// ---- The 19 slots -> HID keycodes (flash table at 0x00011BE4) --------------
/// 160 bytes: `KEYMAP[slot * 8 + row]` = USB HID usage ID, 0 = no key.
pub static KEYMAP: [u8; 160] = [ /* paste bytes 0x00011BE4..0x00011C83 */ ];

#[inline] pub fn hid_code(slot: u8, row: u8) -> u8 {
    KEYMAP[(slot as usize) * 8 + (row as usize)]
}

/// Expand one accepted slot change into per-key press/release events,
/// mirroring FUN_00003292 @ 0x0032A0-0x0032BC exactly.
pub fn expand(slot: u8, old: u8, new: u8, on_key: &mut impl FnMut(u8, bool)) {
    let changed = old ^ new;
    for bit in 0..8u8 {
        if changed & (1 << bit) == 0 { continue; }
        let code = hid_code(slot, bit);
        if code == 0 { continue; }
        on_key(code, new & (1 << bit) != 0); // pressed == bit set
    }
}
```

Timing notes:

* Column assert → read must be ≥ **~6 µs** apart; the stock code's only reason for the wait is
  analogue settling, so treat 6 µs as a floor and validate on hardware (a scope on a row line is
  the fast check).
* Total scan ≈ 135 µs; the stock ISR path takes one column per CT16B3 tick instead (period
  unresolved, §7 U2). Either is far faster than the ≥3-scan debounce needs.
* There are **no delays inside the bit-banged 3-wire link** (`FUN_000043E4`); it relies on GPIO
  write latency alone.
* `deselect_all()` before each `BSET` is mandatory, not stylistic — it is the anti-ghosting
  mechanism, and it also means the bus is never left with a stale column asserted across scans.

---

## 7. What I could not determine

**U1 — whether the 21 column pins and 6 row pins reach the switches directly, or through an
intermediate buffer/diode/transistor network.** The firmware's view is conclusive (19 one-hot
drives, 6 reads), but nothing in the image says what is on the PCB. The 6 µs settle delay and the
vendor's pull-up clearing suggest a passive network with weak pull-downs and possibly diodes. **The
protocol is the same either way** — this affects only whether your pull-up configuration and drive
strength are right. Resolve with a continuity check or a scope.

**U2 — the CT16B3 tick period.** `FUN_00007954` @ `0x007954` writes `PRE (+0x08) = 0x2F`, i.e.
prescale /48, which is 1 µs at 48 MHz PCLK. I could **not** isolate the match value that sets the
interrupt period, because the same function programs CT16B0/1/2/5 for LED PWM using `0x5A……`
magic writes and Ghidra's decompilation of those is unreliable (e.g. `puVar4[8] = DAT_00007a74 +
0xf0` and `*(iVar3 + 0x20) = DAT_00007a9c`). Not needed: the software burst path (`FUN_00005174`)
defines a valid, self-contained rate.

**U3 — the mode switch.** `FUN_00001ef0` @ `0x001EF0` debounces a 3-bit value over a 20-count
window. The earlier note's pins for it (`GPIO1.1`, `GPIO1.2`, `GPIO0.10`) came from the same
mis-read `<<n` idiom, so they are **suspect**; the ISR-clear masks do confirm `GPIO1` pins 1 and 2
are interrupt sources, which is consistent. Not needed to read keys.

**U4 — the physical identity of four keymap entries.** `0x64` (`F13`), `0xAF` (undefined usage),
`0x87` (`International1`) and `0x32` (`Non-US #~`) sit in the table but do not obviously
correspond to keys on a 98-key layout; the rotary encoder and the mode/FN keys are the obvious
candidates. The table is authoritative regardless — copy it verbatim.

**U5 — the part number of the 3-wire display device.** Not identified (needs a teardown or a
logic capture). Explicitly **not on the key path**, so it does not block key reading.

**U6 — `0x00011FB4`.** `FUN_00003004` @ `0x003012`–`0x003032` indexes this 2-byte-per-entry table
with the event's flat index and feeds the result into `0x000120F4` as `slot*8 + bit`. Its contents
do **not** match the identity inverse of `flat = slot*8 + bit` (entry 1 is `(1,0)`, not `(0,1)`),
so either it is a stale/parallel table for a different flat encoding, or Ghidra's rendering of the
consumer is misleading me. It is downstream of the keycode lookup and does **not** affect the bus
protocol or the HID codes.

**U7 — MATRIX-PINS.md's "28 interrupt-enabled pins".** Its `IE`/`IEV` pin lists are built on the
broken `<<n` idiom and on unordered writes inside `FUN_00007800`; `FUN_0000aeb6` (called from the
middle of the GPIO init at `0x007898`) only clears SYS0 bits (`*(u32*)(0xAFFC + 0x2C) &=
0xfffffff0`) and does not program GPIO interrupts. The only trustworthy interrupt facts are the
ISR clear masks (immediates): GPIO0 {10, 19}, GPIO1 {1, 2, 14, 15}, GPIO2 {19}, GPIO3 {19}. Since
`IEV` bit = 1 means *falling* edge / low level per the SVD, and four of those are row lines that go
**high** on a press, one of {my polarity derivation, the IEV values} is wrong — **measure it**.

---

## Evidence

**E1 — the one-hot select switch, `FUN_00003352` @ `0x003352`.** Ghidra renders this function as a
broken indirect call (`WARNING: Could not recover jumptable ... Treating indirect jump as call`)
because of the injected `__ARM_common_switch8` at `0x003366`. Raw read of `0x003352` (400 bytes)
gives:

```
0x3352 f0b5            push {r4,r5,r6,r7,lr}
0x3354 3148            ldr r0,[0x341C]        -> 0x2000016E  (scan index)
0x3356 1025            movs r5,#0x10          ; live constant used by case 0 and 8
0x3358 0678            ldrb r6,[r0,#0x0]      ; switch value = column index
0x335a 0824            movs r4,#0x8           ; live constant used by case 5 and 18
0x335c 0122            movs r2,#0x1           ; live constant used by cases 2,3,15,19
0x335e 3049..314f      ldr r1,[0x3420] = 0x40044000 GPIO0
                       ldr r0,[0x3424] = 0x40048000 GPIO2
                       ldr r7,[0x3428] = 0x40046000 GPIO1
0x3364 3300            movs r3,r6
0x3366 05f0 87fd       bl 0x8E78              ; __ARM_common_switch8
0x336A 15              count = 21
0x336B 0c 10 15 19 1e 23 27 2c 61 65 6a 6f 74 7a 80 86 8a 8f 94 98 9c 0f
```

Helper `0x008E78`: `push {r4,r5}; mov r4,lr; subs r4,#1; ldrb r5,[r4]; adds r4,#1; cmp r3,r5;
bcs; mov r5,r3; ldrb r3,[r4,r5]; lsls r3,#1; adds r3,r4,r3; pop; bx r3` ⇒ target =
`0x336A + 2*table[i]`. All 22 targets land on `ldr rX,[rY,#0x24] / orrs / str rX,[rY,#0x24]`
(`BSET |= mask`) or, for the default, on `0x3388 pop {r4,r5,r6,r7,pc}`.

**E2 — literal pools used above.**

```
0x0033CC 0x20000147 0x0033D0 0x20000139 0x0033D4 0x00011FB4 0x0033D8 0x20000040
0x0033DC 0x200016EF 0x0033E0 0x20000138 0x0033E4 0x000120F4 0x0033E8 0x00008F7C
0x0033EC 0x2000141C 0x0033F0 0x0001236F 0x0033F4 0x20001C6E 0x0033F8 0x00011DD0
0x0033FC 0x2000158F 0x003400 0x200015A3 0x003404 0x200015B7 0x003408 0x200015CB
0x00340C 0x200015F3 0x003410 0x2000013B 0x003414 0x00011BE4 0x003418 0x20001348
0x00341C 0x2000016E 0x003420 0x40044000 0x003424 0x40048000 0x003428 0x40046000

0x0035E0 0x40044000 0x0035E4 0x00040001 0x0035E8 0x40046000 0x0035EC 0x000043FF
0x0035F0 0x40048000 0x0035F4 0x4004A000 0x0035F8 0x20000040

0x0051A0 0x200018A8 0x0051A8 0x40002000 0x0051AC 0x40004000 0x0051B0 0x4000A000
0x0051B4 0x40046000 0x0051B8 0x4004A000 0x0051BC 0x40048000 0x0051C0 0x40044000
0x0051C4 0x00073F00 0x0051C8 0x40006080 0x0051CC 0x2000135B 0x0051D0 0x20000042

0x007A74 0x5A00000F 0x007A78 0x40000080 0x007A7C 0x40002080 0x007A80 0x40004080
0x007A84 0x4000A080 0x007A94 0x004005E0 0x007A98 0x40006000 0x007A9C 0x5A0007FF
0x007AA0 0xE000E280 0x007AA4 0xE000E100
```

(Read with `read_memory`; every pool word above was transcribed from raw bytes, not from
decompiler text.)

**E3 — `FUN_000034C8` @ `0x0034C8` (columns → outputs), verbatim disassembly.**

```
0x34C8 ldr r0,[0x35E0]   ; GPIO0
0x34CA ldr r1,[r0,#0x4]  ; MODE
0x34CC movs r2,#0xff
0x34CE orrs r1,r2
0x34D0 str r1,[r0,#0x4]  ; GPIO0_MODE |= 0xFF
0x34D2 ldr r0,[0x35E8]   ; GPIO1
0x34D4 ldr r1,[r0,#0x4]
0x34D6 ldr r2,[0x35E4]   ; 0x00040001
0x34D8 orrs r1,r2
0x34DA str r1,[r0,#0x4]  ; GPIO1_MODE |= 0x40001
0x34DC ldr r0,[0x35F0]   ; GPIO2
0x34DE ldr r1,[r0,#0x4]
0x34E0 ldr r2,[0x35EC]   ; 0x000043FF
0x34E2 orrs r1,r2
0x34E4 str r1,[r0,#0x4]  ; GPIO2_MODE |= 0x43FF
0x34E6 ldr r0,[0x35F4]   ; GPIO3
0x34E8 ldr r1,[r0,#0x4]
0x34EA str r1,[r0,#0x4]  ; no-op
0x34EC bx lr
```

`FUN_000034AE` @ `0x0034AE` = the same three masks written to `BCLR` (`0x28`) plus `GPIO3_BCLR = 0`.

**E4 — `FUN_000034EE` @ `0x0034EE` (one sample), verbatim disassembly.** Steps 1–10 of §1:
`bl 0x34C8` (`0x34F2`), `bl 0x34AE` (`0x34F6`), `bl 0x3352` (`0x34FA`), `movs r0,#0xa` +
`bl 0x6F1A` (`0x34FE`), then six `ldr …DATA; lsls; bpl` tests at `0x3504/0x350E/0x3518/0x3522/
0x352E/0x353A` building bits 0..5 (see §2.2).

**E5 — `FUN_000032E0` @ `0x0032E0` (debounce), verbatim disassembly.** 54 instructions,
`0x32E0`–`0x3350`; logic transcribed in §4; the three array bases are the literal `0x003418 =
0x20001348` plus offsets `0x00` (counter), `0x13` (current), `0x26` (reference), `0x39` (last
accepted). The event records go to `0x20000040 + 3` (index), `+0x34` (state), `+0x38` (changed
mask) — `0x003328`–`0x00332C`.

**E6 — `FUN_00003292` @ `0x003292` (slots → keycodes).** 37 instructions, `0x3292`–`0x32DE`;
the load `ldr r1,[0x00003414]` / `ldrb r0,[r1,r0]` / `strb r0,[r4,#0x6]` at
`0x32B8`/`0x32BA`/`0x32BC` is the keycode fetch, with `flat = slot*8 + bit` computed at
`0x32A4`–`0x32AA`.

**E7 — the keymap table at `0x00011BE4`.** 160 raw bytes read with `read_memory`, transcribed
column-for-column into §3.3; 97 non-zero entries counted mechanically.

**E8 — `FUN_000043E4` @ `0x0043E4` (3-wire bit-bang).** 8 bits MSB-first, `GPIO3.5` clock,
`GPIO3.6` data out, `GPIO1.22` data in; callers and register traffic in §5.

**E9 — display strings.** `0x00E408 "USB Mode"`, `0x00E410 "Link"`, `0x00E420 "USB Cable!"`,
`0x00E43C "Success!"`, `0x00E454 "Fail!!!"`, `0x00E468 "2.4G"`, `0x00E470 "BT1"`,
`0x00E474 "BT2"`, `0x00E478 "BT3"`, `0x00E47C "Linking"`, `0x00E888 "Pairing"`; pointer table
hits at `0x00E3E4 → 0x00E40B`, `0x00E884 → 0x00E468`, `0x011C48 → 0x00E487`, `0x0162B8 →
0x00E4C5`, etc.

**E10 — CT16B3 register identities.** From `SN32F290.patched.svd`: `SN_CT16B3` base
`0x40006000`; `RIS` `+0xA8`, `IC` `+0xAC`, `PRE` `+0x08`, `MR0` `+0x20`, `PWMCTRL` `+0x98`.
`FUN_000050BE` uses `0x400060A8`/`0xAC`; `FUN_00007954` writes `+0x08 = 0x2F`.

**E11 — corrections issued against [MATRIX-PINS.md](MATRIX-PINS.md).**

| MATRIX-PINS claim | Correct value | Why |
|---|---|---|
| "no GPIO key matrix scan anywhere" (§Summary, §3) | a 6×19 one-hot-scanned matrix exists | §1, §2 |
| rows = GPIO1.{15,14,11}, GPIO3.11, GPIO0.{11,10} (§1.2, E7) | rows = GPIO1.{14,15,19}, GPIO3.19, GPIO0.{19,18} | `lsls + bpl` tests bit `31-n`, not bit `n` |
| `FUN_00005174` fills `0x20000095` (§1.2) | it fills `0x2000135B` (`DAT_000051CC = 0x2000135B`, `0x005184` `strb r0,[r5,r1]`) | disassembly of `0x5174` |
| `0x2000136E` = "raw" (§1.1) | `0x2000136E` is the reference tracker; the raw sample is `0x2000135B` | disassembly of `FUN_000032E0` |
| GPIO0/1/2 `CFG1` pull-up decoding (§Summary table) | GPIO1 `CFG1 = 0x800A` ⇒ pins 16,17 pull-up OFF, 18,19 ON; GPIO0/GPIO3 `CFG1` writes are out-of-range no-ops | `CFG1` fields are bits 0–7 for pins 16–19 only |
| "the off-chip link is a multi-wire parallel interface … to an off-chip keyboard controller" (§1.2) | it is a directly driven matrix; the off-chip 3-wire device is the display | §1, §5 |

---

*Analysis performed read-only against the stock image via Ghidra (`S98Pro_SN32F290_stock.bin`) and
raw-byte reads. No device was connected, nothing was flashed, and the only file created is this
one.*
