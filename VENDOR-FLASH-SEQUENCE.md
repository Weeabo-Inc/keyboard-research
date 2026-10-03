# Vendor Flash Sequence — SN32F290

**Status:** complete, cross-validated against live hardware. **Nothing was flashed.**
**Date:** 2026-10-03

---

## Provenance and a licensing note, read this first

The **sequence and constants** below come from reading the source of
[SonixFlasherC](https://github.com/SonixQMK/SonixFlasherC) (a local checkout is at
`research/SonixFlasherC/`). That project is **GPL-3.0**.

**Reading it to learn a protocol is fine. Copying its code into our MIT project is not.**
Protocol facts — command bytes, field offsets, a checksum algorithm — are not copyrightable,
and the numbers below are facts. The *implementation* in `sonix-rs` must be written fresh.
Do not paste from that tree.

The **transport framing** below does not come from SonixFlasherC at all. It was measured on
real hardware and recovered from the vendor's own binary, and it is what our device actually
answers. See "Transport" for the one place the two references disagree.

---

## Transport, and the one thing that differs

SonixFlasherC writes **output reports**. Our AULA S98Pro **only answers feature reports**:

```
measured: output report  -> accepted, never answered
measured: feature report -> answered          (this is the transport we use)
vendor:   HidD_SetFeature is the ONLY write in the whole program
```

So: same protocol, same bytes, **different report type**. Everything below is expressed the
way we must actually send it — `FeatureReportByteLength = 65` = 1 report ID byte + 64 payload.

```
SETFEATURE: [00][ payload[0..63] ]        <- report id 0x00, then 64 payload bytes
GETFEATURE: -> 65 bytes; strip the report id, work with payload[0..63]
```

---

## Reply format (confirmed against your keyboard)

Every command's reply has this shape:

```
payload[0..3]   CMD_VERIFY(cmd) = (0x55AA << 8) | cmd      e.g. cmd 0x04 -> 04 AA 55 00
payload[4..7]   CMD_ACK = 0xFAFAFAFA                       -> FA FA FA FA
payload[8..63]  command specific data
```

**Your keyboard's live ISP reply, captured earlier tonight:**

```
00 01 AA 55 00 FA FA FA FA 20 05 00 00 FF FF 5A 5A ...
   ^^^^^^^^^^^ ^^^^^^^^^^^
   cmd AA 55 00  FA FA FA FA            <- exactly the format above
```

That is not a coincidence, and it is the strongest evidence in this document.

---

## 1. ERASE

`flash_erase()` in `SonixFlasherC/src/flash.c`.

```
SETFEATURE  [00] 04 AA 55 00 00 00 00 00 [01 00] 00 ... 00
                 ^^ ^^^^^ ^^^^^^^^       ^^^^^^^
                 |  |     |              end page, u16 LE = 256 (0x0100)
                 |  |     start page, u16 LE = 0
                 |  CMD_BASE 0x55AA
                 CMD_ENABLE_ERASE 0x04
```

| Payload offset | Width | Value |
|---|---|---|
| `[0]` | u8 | `0x04` `CMD_ENABLE_ERASE` |
| `[1..2]` | u16 LE | `0x55AA` `CMD_BASE` |
| `[3]` | u8 | `0x00` |
| `[4..5]` | u16 LE | start page = **`0`** |
| `[6..7]` | u16 LE | `0` |
| `[8..9]` | u16 LE | end page = **`256`** (`ROM_PAGES_F290`) |
| `[10..63]` | | zero |

```
GETFEATURE  expect payload[0..3] == 04 AA 55 00
            expect payload[4..7] == FA FA FA FA
            CHECK  payload[8..9] (u16 LE) == 0x0000   <- blank checksum for F290
```

**A whole-chip erase is page 0 through page 256.** Note that 256 pages of 1 KB is the entire
256 KB flash, so this is a **full-chip erase, not a range**. There is no partial erase here.

### DANGER

`[8..9]` is the *last* erase page, which contains the code-option/security word at
`0x3FFFC` (`0xAAAA5555` in the stock image). **A full erase destroys it.** Section 5 covers
what to do about that.

---

## 2. PROGRAM

Two phases: an enable, then raw data.

### 2a. Enable program mode

```
SETFEATURE  [00] 05 AA 55 00 [offset u32 LE] [total_chunks u32 LE] 00 ... 00
```

| Payload offset | Width | Value |
|---|---|---|
| `[0]` | u8 | `0x05` `CMD_ENABLE_PROGRAM` |
| `[1..2]` | u16 LE | `0x55AA` |
| `[3]` | u8 | `0x00` |
| `[4..7]` | u32 LE | **offset** — `0` for a full image |
| `[8..11]` | u32 LE | **total_chunks** = `ceil(fw_size / 64)` |
| `[12..63]` | | zero |

```
GETFEATURE  expect payload[0..3] == 05 AA 55 00
            expect payload[4..7] == FA FA FA FA
```

For our 262,144-byte stock image: **offset 0, total_chunks = 4096.**

### 2b. Data

**No header. No address. No command byte.** This is the part that surprises people:

```
for each 64-byte chunk of the image:
    SETFEATURE  [00][ 64 raw bytes of firmware ]
```

Each write is exactly 65 bytes on the wire. The device advances its own internal pointer.
There is no per-chunk acknowledgement during the transfer — the loop just writes.

### 2c. Verify

```
GETFEATURE  expect payload[0..3] == 05 AA 55 00
            expect payload[4..7] == FA FA FA FA
            CHECK  payload[60..63] (u32 LE) == last_chunk
            CHECK  payload[8..9]  (u16 LE) == running checksum   (only when offset == 0)
```

| Value | How it is computed |
|---|---|
| `last_chunk` | the **last 4 bytes of the final data chunk**, read as u32 LE |
| `running checksum` | sum over the whole image, see below |

**Checksum algorithm** (`checksum_calculate`): sum every little-endian u16 pair; if the length
is odd, add the final byte on its own. Accumulated across all chunks. 16-bit, wraps.

```python
def fw_checksum(data: bytes) -> int:
    s = 0
    i = 0
    while i + 1 < len(data):
        s = (s + (data[i] | (data[i + 1] << 8))) & 0xFFFF
        i += 2
    if i < len(data):
        s = (s + data[i]) & 0xFFFF
    return s
```

---

## 3. Standalone checksum read

```
SETFEATURE  [00] 06 AA 55 00 00 ... 00
GETFEATURE  expect 06 AA 55 00 + FA FA FA FA
            payload[8..9] = flash checksum (u16 LE)
```

Useful as a **baseline before and after** a flash.

---

## 4. Return to user mode

```
SETFEATURE  [00] 07 AA 55 00 00 ... 00
```

**This is already implemented in `sonix-rs isp-exit`, and it is the command that brought your
keyboard back.** It disconnects the device, which is expected — `HidD_GetFeature` will fail
with Win32 error 1167 (`ERROR_DEVICE_NOT_CONNECTED`). **That failure is the success signal.**
Do not treat it as an error.

---

## 5. The code option / security word — THE DANGEROUS PART

| Fact | Source |
|---|---|
| Stock image's last flash word at `0x3FFFC` is `0xAAAA5555` | read from the image |
| A full erase (pages 0-256) **destroys** it | section 1 — page 256 is the last page |
| `CS_LEVEL_0_VAL2 = 0xFFFF` is what SonixFlasherC writes for CS0 on F290-class parts | `chip.c` profile for `CHIP_F290` |
| There is an ISP opcode for reading it: `0x04`/`0x29` code option, and `0x09` checksum reads | vendor tool opcode map |

**What this means in plain terms:** erase the chip and `0xAAAA5555` is gone. Whatever you
write afterwards must put a valid code-option word back, or the part may come up with the
wrong security setting or refuse to run.

**The safe procedure:**

1. **Before erasing**, read the code option over ISP and record it. It is currently
   `0xAAAA5555`.
2. After programming, **deliberately write the code option back**.
3. **Never write a non-zero security level (CS1/CS2/CS3) during development.** Code security
   can block SWD readout, and SWD is the only recovery path we might otherwise have. This is
   a one-way door on a device whose case we are not opening.

The exact opcode and argument layout for the code-option *write* is **not yet confirmed from
the vendor binary** — SonixFlasherC does not perform a code-option write at all in the paths
we read. **Treat this as the single largest open risk in the flash plan.**

---

## 6. Exact byte sequence for reimplementation

```
# --- entry (already proven working) ---
SETFEATURE  [00] AA 42 89 5A FF 71 62 CC 00 x56      # write only, no read
sleep 150 ms
SETFEATURE  [00] 01 AA 55 00 00 x60
GETFEATURE  expect payload[0..3] == 01 AA 55 00

# --- baseline ---
SETFEATURE  [00] 06 AA 55 00 00 x60
GETFEATURE  -> record payload[8..9] as baseline_checksum

# --- READ CODE OPTION HERE (opcode not yet confirmed - DO NOT SKIP) ---

# --- erase ---
SETFEATURE  [00] 04 AA 55 00 00 00 00 00 00 01 00 x54
GETFEATURE  expect payload[0..3] == 04 AA 55 00
            expect payload[4..7] == FA FA FA FA
            expect payload[8..9] == 00 00

# --- enable program ---
SETFEATURE  [00] 05 AA 55 00 00 00 00 00 00 10 00 00 00 x52
                                          ^^^^^^^^^^^^^ total_chunks = 4096
GETFEATURE  expect payload[0..3] == 05 AA 55 00
            expect payload[4..7] == FA FA FA FA

# --- data, 4096 chunks of 64 bytes, no header ---
for chunk in image:
    SETFEATURE  [00] <64 bytes>

# --- verify ---
GETFEATURE  expect payload[0..3] == 05 AA 55 00
            expect payload[4..7] == FA FA FA FA
            expect payload[60..63] == last_chunk
            expect payload[8..9]   == running_checksum

# --- write code option back (NOT YET CONFIRMED) ---

# --- exit ---
SETFEATURE  [00] 07 AA 55 00 00 x60
# device disconnects; Win32 error 1167 on any further read is EXPECTED
```

---

## 7. Unknowns and risks

1. **The code-option write opcode is unconfirmed.** The vendor binary has it (opcode family
   `0x03`/`0x04`/`0x08`), but the argument semantics are not yet pinned down. **This is the
   top risk.** Writing a wrong security value could permanently disable SWD.
2. **Whether the feature-report dialect accepts 4096 sequential 65-byte writes.** SonixFlasherC
   does it over output reports. Our device answered feature reports for every command we
   tried, but **nobody has pushed 4096 data chunks through it.** This should be tested with a
   small, harmless payload before a real image.
3. **Timing between chunks.** SonixFlasherC has `IO_DELAY_SEC = 1` and `RETRY_DELAY_MS = 100`
   with `MAX_ATTEMPTS = 5`. The vendor tool has its own delays. Neither is confirmed correct
   for the feature-report path.
4. **F290 is listed in SonixFlasherC but may be untested there.** `CHIP_F290` appears in the
   profiles with `variant 0`. Verify against our own chip rather than assuming.
5. **The erase is full-chip.** There is no partial erase. If the plan goes wrong at chunk 2000
   of 4096, the flash is half-written and the application is gone.

---

## 8. Reproduction

Read-only, no hardware:

```console
$ python -c "..."            # checksum algorithm, section 2c
$ grep -n "CMD_ENABLE"  research/SonixFlasherC/include/hid_io.h
$ grep -n "F290"        research/SonixFlasherC/include/chip.h
$ sed -n '14,50p'       research/SonixFlasherC/src/flash.c
```

Files consulted:

| File | What it gave |
|---|---|
| `SonixFlasherC/src/flash.c` | erase, program, verify sequences |
| `SonixFlasherC/src/hid_io.c` | report framing and reply validation |
| `SonixFlasherC/src/checksum.c` | the checksum algorithm |
| `SonixFlasherC/include/config.h` | `REPORT_SIZE 64`, retry counts, delays |
| `SonixFlasherC/include/hid_io.h` | command opcodes |
| `SonixFlasherC/include/chip.h` | F290 pages, blank checksum, security values |
| `SonixFlasherC/include/flash.h` | `LAST_CHUNK_OFFSET = 60` |

---

## 8b. VENDOR CONFIRMATION — the real programming routine

Recovered from the vendor binary. The dispatcher is `FUN_00406bb0` (workflow 9, our chip), which
calls `FUN_00407210` — **the actual flash routine for chip index 17 (SN32F290)**.

This is the authoritative sequence. Where it and section 6 differ, **trust this one.**

### The routine, in order

```c
// --- guard: firmware must be present ---
if (*(int *)(ctx + 0x14) == 0) return;          // no image loaded -> bail

// --- security level decode ---
sec = *(int *)(ctx + 0x20);                     // config setting
if (sec == 0)      val = (chip==0xd||chip==0xe) ? 0x0000 : 0xFFFF;   // <-- OURS: 0xFFFF
else if (sec == 1) val = 0x5A5A;                // CS1
else if (sec == 2) val = 0xA5A5;                // CS2
else if (sec == 3 && (chip==0x11||chip==0x12)) val = 0xC3C3;
else               val = 0x55AA;                // CS3
ctx[10..11] = val;                              // stored little-endian

// --- chip 13 and 14 skip the erase block. WE DO NOT. ---
if (chip != 0xd && chip != 0xe) {

    FUN_0040abc0(local_414);       // opcode 0x02, 64-byte pre-erase handshake
                                   // fail -> "Codeoption Erase Protocol Handshake Error!!"

    FUN_0040ac20(uVar5);           // opcode 0x04  <-- CHIP ERASE
                                   // fail -> "Chip Erase Protocol Handshake Error!!"

    n = 1;                         // wait loop, 101 iterations
    do {
        progress(n);
        for (i = 0; i < local_418; i++) small_delay();
        n++;
    } while (n < 0x65);            // 0x65 = 101

    FUN_00427770(vid, pid, buf, 0x40);              // READ 64 bytes
    if (buf[8..9] != *(u16*)(chipEntry + 0x25))     // BLANK CHECK
        fail("Blank Check Failed!!");
    // else "Blank Check OK!!"
}

FUN_0040af00(...);                 // opcode 0x05  <-- ENABLE PROGRAM
                                   // fail -> "Program Failed!!"

// --- data write: 64 raw bytes at a time, ascending offset, NO header ---
total = local_414;
offset = 0;
for (i = 0; i < total; i++) {
    FUN_00427620(vid, pid, image + offset, 0x40);   // <-- WRITE
    small_delay();
    offset += 0x40;
}

FUN_00427770(vid, pid, buf, 0x40);                  // READ 64 bytes
if (computed_checksum(image) == buf[8..9]) {        // VERIFY

    // --- write the code option back ---
    if (chip != 0xe && chip != 0x11 && chip != 0x12 &&
        (chip != 0xd || ctx[4] != 0)) {
        FUN_0040aac0();            // opcode 0x03  <-- CODE OPTION WRITE
                                   // fail -> "Code Option Program Failed!!"
    }

    // --- reset ---
    if (ctx[0x2e] != 0) {
        r = FUN_0040b270();        // opcode 0x07  <-- RETURN TO USER MODE
        if (r != 5) fail("Reset Failed!!");
    }
} else {
    fail("Program Failed!!");      // checksum mismatch
}
```

### What this settles

| Question | Answer | Confidence |
|---|---|---|
| Erase opcode | **`0x04`** via `FUN_0040ac20` | high — matches SonixFlasherC exactly |
| Pre-erase command | **`0x02`**, 64-byte, via `FUN_0040abc0` | high |
| Enable program | **`0x05`** via `FUN_0040af00` | high |
| Data framing | **64 raw bytes, no header**, offset ascending in 0x40 steps | high |
| Verify field | reply bytes **[8..9]** | high — `local_408` is exactly 8 bytes into `local_410` |
| Code option write | **`0x03`** via `FUN_0040aac0` | high |
| Return to user mode | **`0x07`** via `FUN_0040b270` | high — already proven on hardware |
| CS0 value **for SN32F290** | **`0xFFFF`** — *not* `0x0000` | high |
| Erase wait | 101 iterations with a per-chip delay count from descriptor `+0x1f` | medium |

**The CS0 value is the important one.** Section 5 assumed `0xFFFF` from SonixFlasherC's
`CS_LEVEL_0_VAL2`; the vendor code confirms it, and shows it is chip-specific: chips 13/14 get
`0x0000`, everything else including ours gets **`0xFFFF`**.

### The security guard, and what your keyboard is reporting

`FUN_00406bb0` refuses to proceed in two cases:

```c
if (security_word == 0x5A5A) {
    "Device security level has been changed, unsupport ISP Download !!"
    FUN_0040b270();   // returns to user mode, aborts
}
if (security_word == 0x55AA) {
    "Security = CS3, Unsupport ISP Download !!"
    // refuses outright
}
```

**Your keyboard's live ISP response ended with `... FF FF 5A 5A`, i.e. `payload[14..15] = 0x5A5A`.**

`0x5A5A` is **CS1**. This was flagged as needing resolution before any erase. **It has since been
resolved, and the news is good. See section 8c.**

---

## 8c. RESOLVED: the security word IS `reply[14..15]`, and CS1 does not block us

### Where the security word actually comes from

`FUN_0040a760(param_1, param_2)` is the ISP check. Decompiled:

```c
FUN_0040a760(ctx+4, ctx+8)
{
    *(u8*)(ctx+4) = 0x01;  *(u8*)(ctx+5) = 0xAA;
    *(u8*)(ctx+6) = 0x55;  *(u8*)(ctx+7) = 0x00;         // build "01 AA 55 00"

    FUN_00427620(vid, pid, ctx+4, 0x40, 0);              // WRITE 64 bytes
    FUN_0040a5d0();                                      // delay
    FUN_00427770(vid, pid, ctx+0x44, 0x40, 0);           // READ 64 bytes into ctx+0x44

    if (*(u32*)(ctx+4) == *(u32*)(ctx+0x44)) {           // echo check on 4 bytes
        *param_1 = *(u32*)(ctx + 0x4c);                  // ctx[4..7]  = reply[8..11]
        *param_2 = *(u32*)(ctx + 0x50);                  // ctx[8..11] = reply[12..15]
        return 5;
    }
    return 2;
}
```

`0x4c = 0x44 + 8` and `0x50 = 0x44 + 12`. Therefore:

| Context field | Source | Meaning |
|---|---|---|
| `ctx[4..7]` | reply `[8..11]` | chip ID, compared byte-for-byte against the per-chip table |
| `ctx[8..11]` | reply `[12..15]` | second dword, **high half is the security word** |
| `ctx[10..11]` | reply `[14..15]` | **THE SECURITY WORD** |

**So the original concern was correct: `payload[14..15]` really is the security word, and our
device really is reporting CS1 (`0x5A5A`).** It was not a false alarm about the wrong field.

### Why CS1 does not block us

`FUN_00406bb0` has two security guards, and neither applies:

```c
// GUARD 1 - cannot apply to us: chip index 13 only
if (chipIndex == 0xd) {
    if (security == 0x5A5A || security == 0xA5A5) {
        "Device security level has been changed, unsupport ISP Download !!"
        FUN_0040b270();                       // abort
    }
}

// GUARD 2 - does not trigger: needs exactly 0x55AA
if ((chip == 0x11 || chip == 0x12) ||
    (ctx[10] != 0xAA || ctx[11] != 0x55)) {
        // PROCEED
} else {
        "Security = CS3, Unsupport ISP Download !!"
}
```

- **Guard 1** is gated on `chipIndex == 0xd`. **Ours is 17.** Not applicable.
- **Guard 2** proceeds unless the word is exactly `0x55AA` (CS3). Ours is `0x5A5A`:
  `ctx[10] = 0x5A != 0xAA` → the condition is true → **proceed.**

**Conclusion: CS1 is not a blocker for SN32F290. Only CS3 (`0x55AA`) would stop the erase.**

### Chip identity, cross-checked

| Reply field | Live value | Meaning |
|---|---|---|
| `[8..11]` | `20 05 00 00` | family `0x20` = 32-bit, chip version `0x05` = SN32F29X |
| `[12..13]` | `FF FF` | low half of dword 2 |
| `[14..15]` | `5A 5A` | security word = CS1 |

The device's `[8..11]` matches the descriptor-derived fingerprint for chip index 17. **Identity
confirmed from three directions:** the vendor's filename, the chip's own version response, and
the tool's per-chip ID table.

**This item is closed. The security word is confirmed readable and non-blocking.**

### Still not confirmed

- **Whether `0x02` before the erase is mandatory.** The vendor sends it; SonixFlasherC does not.
  Treat the vendor as authoritative and send it.
- **`local_414` and `local_418`** are read from a runtime-populated chip parameter table at
  `PTR_PTR_005b2bdf + chipIndex * 0x33`, fields `+0x2b` and `+0x1f`. Their values were not
  resolved statically. `local_414` is the chunk count; `local_418` is a delay count.
- **What `FUN_00409880(image)` computes** exactly. It is the host-side checksum compared against
  the device's returned value. Almost certainly the section-2c algorithm, but unverified.

---

## 9. What is NOT in this document

- **No confirmation that the feature-report path carries bulk data.** Section 7 item 2.
- **No code-option write.** Section 7 item 1. Do not guess it.
- **No evidence that the ROM self-recovers from a half-written image.** Treat as false.

**Do not run an erase until section 7 items 1 and 2 are closed.** Everything up to and
including the baseline checksum read is safe to do on real hardware; the erase is the point
of no return.
