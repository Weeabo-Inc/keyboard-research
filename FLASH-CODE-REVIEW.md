# Flash Implementation Review

**Reviewer:** adversarial pass over `sonix/src/{flash,isp,main,protocol,device}.rs`
**Date:** 2026-10-03
**Method:** read-only. All source and docs read in full; `cargo test` (44/44 pass) and `cargo build` run
(no hardware); the vendor binary `S98PRO Firemware.exe` decompiled read-only in Ghidra; the stock dump
`keyboard/stock/S98Pro_SN32F290_stock.bin` read directly. **The `sonix-rs` binary was not executed.**

Evidence sources used beyond the five source files: `research/VENDOR-FLASH-SEQUENCE.md`,
`keyboard/ISP-ENTRY-SOLVED.md`, `keyboard/ISP-ENTRY-PROTOCOL.md`,
`research/SonixFlasherC/{src/flash.c,src/checksum.c,src/hid_io.c,include/chip.h,include/config.h,src/cli.c}`,
and decompilations of `FUN_0040aac0`, `FUN_0040abc0`, `FUN_0040ac20`, `FUN_0040ade0`, `FUN_0040af00`,
`FUN_00407210`, `FUN_004097d0` plus the call sites at `0x004070a8`, `0x004073aa`, `0x0040786f`.

---

## Verdict

**DO NOT RUN** `flash-probe`, and do not implement the erase/program path from the current plan.

Not because the erase command exists — it does not. `main.rs` has **no erase and no program command**:
`flash::erase_payload` (`flash.rs:148`) and `flash::code_option_payload` (`flash.rs:169`) are reachable
only from the `flash-info` *printout* (`main.rs:543`, `main.rs:552`). The one command in the shipped
binary that touches flash is `flash-probe`, and it is unguarded, unverified, and built on an addressing
assumption nobody has tested.

Three things must be fixed before anything is written to this chip:

1. `code_option_payload` puts the security word in the wrong half of the 4 data bytes (`flash.rs:169-173`).
   If that payload is ever sent, it programs the *code option* field with the security value and leaves
   the security field at `0x0000`. Confirmed against the vendor's `FUN_0040aac0` and both of its call sites.
2. The code-option write is in the wrong place in the sequence, and the review premise behind it
   ("the vendor skips it for 0x11, our chip is 17") is half wrong in a way that matters — see CRITICAL-2.
3. `flash-probe` writes to flash without an erase, without a blank check, and at an offset whose units
   are unproven, and its success test is arithmetically incapable of ever passing.

The read-only commands (`list`, `enum`, `info`, `isp-entry`, `isp-exit`, `isp-burst`, `vversion`, `vcmd`,
`checksum`, `flash-info`) are unaffected by these findings. `flash-info` is genuinely send-nothing.

---

## CRITICAL findings

### CRITICAL-1 — `code_option_payload` sends the security word in the wrong half of the field

`flash.rs:169-173`

```rust
pub fn code_option_payload(value: u16) -> [u8; 64] {
    let mut p = cmd4(OP_CODE_OPTION, 0);          // 03 AA 55 00
    p[4..6].copy_from_slice(&value.to_le_bytes()); // <-- value at [4..5]
    p
}
```

The vendor's opcode-`0x03` builder `FUN_0040aac0` (@`0x0040aac0`) writes **four data bytes at payload
`[4..7]`**, copied from a buffer whose address arrives in EAX:

```c
*(u8*)(ESI + 4)  = 3;  *(u8*)(ESI + 5) = 0xaa;
*(u8*)(ESI + 6)  = 0x55;  *(u8*)(ESI + 7) = 0;      /* 03 AA 55 00 */
*(u8*)(ESI + 8)  = in_EAX[0];   /* ESI+4 is payload[0], so these four */
*(u8*)(ESI + 9)  = in_EAX[1];   /* are payload[4], [5], [6], [7]      */
*(u8*)(ESI + 10) = in_EAX[2];
*(u8*)(ESI + 0xb) = in_EAX[3];
```

At **both** call sites the source buffer is `ctx + 8`:

* `0x004070a5  LEA EAX,[EBP + 0x8]` (in `FUN_00406bb0`)
* `0x00407869  LEA EAX,[EDI + 0x8]` (in `FUN_00407210`, our chip's routine)

and the security word is stored at `ctx[10..11]`, not `ctx[8..9]` — visible directly in the dispatcher:

```
0040709a  MOV byte ptr [EBP + 0xa], AL      ; ctx[10] = val low
004070a0  MOV byte ptr [EBP + 0xb], AL      ; ctx[11] = val high   (AL = EAX>>8)
```

(`VENDOR-FLASH-SEQUENCE.md:339` records the same field for `FUN_00407210`: `ctx[10..11] = val`.)

So the vendor's bytes are `03 AA 55 00 <ctx8> <ctx9> <sec_lo> <sec_hi>`, and **the security value
belongs at payload `[6..7]`**. `flash.rs` sends `03 AA 55 00 FF FF 00 00` for CS0 — the two u16 fields
are swapped. The device would receive `0xFFFF` in the code-option field and `0x0000` (chip.h's
`CS_LEVEL_0_VAL1`) in the security field.

This is corroborated by the project's own other builder: `protocol.rs:171-176`
(`req_set_code_security`) already models opcode `0x03` as *code option at `[4..5]`, cs_value at `[6..7]`*
— the same split my decompilation shows. `flash.rs` contradicts it.

Blast radius: the code-option word is not a spare field. Section 5 (`VENDOR-FLASH-SEQUENCE.md:201-226`)
calls the code-option write "the single largest open risk" and warns that security settings are a
one-way door; §9 (`:527`) says in terms: **"No code-option write. Do not guess it."** This code guesses it.
`main.rs:552` also prints the wrong bytes in the plan, which is how a bad payload gets copied by hand.

### CRITICAL-2 — the code-option write is in the wrong place, and 0x11 **is** 17

`main.rs:539-553` (the plan) and `flash.rs:169` (the payload) place the code-option write as **step 8,
after verify**. The vendor's real order for our chip is different, and there are **two** code-option
sites in `FUN_00407210` with *opposite* guards. Decompiled `FUN_00407210`, control flow:

```
chip = *(int*)(param_1 + 4)                       ; the 0x33-stride descriptor index
sec  = *(int*)(param_1 + 0x20)
  sec==0 -> (chip==0xd||chip==0xe) ? 0x0000 : 0xFFFF      ; ours: 0xFFFF
  sec==1 -> 0x5A5A   sec==2 -> 0xA5A5
  sec==3 && (chip==0x11||chip==0x12) -> 0xC3C3   else 0x55AA
ctx[10..11] = val
if (chip != 0xd && chip != 0xe) {
    FUN_0040abc0()                  ; 0x02 handshake          <- step A
    ... checks ...
    if (chip == 0x11 || chip == 0x12) {          <-- SITE 1 (0x004073aa)
        FUN_0040aac0()              ; 0x03 CODE OPTION, BEFORE THE ERASE
    }
    FUN_0040ac20()                  ; 0x04 CHIP ERASE
    wait 101 x delay
    read 64 -> blank check  reply[8..9] == table[+0x25]
}
FUN_0040af00()                      ; 0x05 enable program
data loop, 0x40 per chunk, delay per chunk
read 64; if (FUN_00409880(image) == reply[8..9]) {
    if (chip != 0xe && chip != 0x11 && chip != 0x12 &&   <-- SITE 2 (0x0040786f)
        (chip != 0xd || ctx[4] != 0)) {
        FUN_0040aac0()              ; 0x03 CODE OPTION, AFTER VERIFY
    }
    if (ctx[0x2e] != 0) { FUN_0040b270(); }   ; 0x07 return to user mode
}
```

Instruction-level confirmation of both guards: SITE 1 at `0x00407394-0x00407397`
(`CMP EAX,0x11 / JZ 0x004073a2` → takes the 0x11 case *into* the write) and SITE 2 at
`0x00407846-0x00407861` (`CMP EBX,0x11 / JZ 0x0040795f` → jumps *past* the write, landing on the
`FUN_0040b270` reset step).

**Our chip index is 17, and 17 == 0x11.** The field `*(param_1+4)` is the descriptor index: it is the
same value used to index the 0x33-stride table (`*(int*)((int)&PTR_PTR_005b2bdf + *(int*)(param_1+4) * 0x33)`),
and `0x005a99fc + 17*0x33 = 0x005a9d5f`, which `ISP-ENTRY-PROTOCOL.md:188-208` proves is the SN32F290
descriptor. So:

* The review premise "the vendor skips the code-option write for 0x0e, 0x11, 0x12; our chip is 17" is
  **half wrong**. It is true of SITE 2 only. SITE 1 writes the code option **only for 0x11 and 0x12** —
  i.e. our chip is one of exactly two parts that write it early, between the 0x02 handshake and the 0x04 erase.
* `sonix-rs` writes it unconditionally (there is no chip-index logic anywhere in `flash.rs`), at SITE-2
  timing, which is the branch the vendor skips for our chip.

`VENDOR-FLASH-SEQUENCE.md:379-383` (§8b) compresses both sites into one post-verify `if`, so the doc that
was used as ground truth does not contain this distinction. Anything built from §8b alone gets the order wrong.

**Recommendation:** do not send opcode `0x03` at all until (a) the `[4..5]` field's source is identified —
see "What I could not determine" #1 — and (b) someone can explain why the vendor writes the code option
and then immediately full-chip-erases the page that holds it (`FUN_0040ac20` erases pages 0..256, and the
stock code-option word at `0x3FFFC` is inside that range). I cannot reconcile those two facts, and that
alone is enough to keep the write out of the implementation.

### CRITICAL-3 — `flash-probe` can write at address 0, which is unrecoverable

`main.rs:561-624`. The claim under review: *"write ONE chunk into unused flash … 0x30000, well clear"*.
Attack:

* **The target is genuinely blank — I verified it.** In `keyboard/stock/S98Pro_SN32F290_stock.bin`:
  bytes `0x30000..0x30040` are all `0xFF`; the last non-`0xFF` byte below `0x17000` is at `0x167F3`;
  `0x17000..0x30000` is entirely `0xFF`; `0x3FFFC` holds `55 55 AA AA`. So *if the write lands where
  the code intends*, the damage is 64 bytes of wasted flash.
* **The offset's units are not established.** `PROBE_OFFSET` (`flash.rs:208`) is passed as the u32 at
  payload `[4..7]` of `enable_program_payload` (`flash.rs:158-163`). Section 2a/§6 and SonixFlasherC
  (`flash.c:107 mem_write_u32_le(buf+4, offset)`, `cli.c:130 "-o, --offset ADDR"`) treat that field as
  an image/flash byte offset with `0` meaning "whole image from the start". **The vendor never sends a
  non-zero value there at all**: `FUN_0040af00` (@`0x0040af00`) zeroes `payload[4..7]` unconditionally
  and writes only a **u16** chunk count at `[8..9]` (`*(u8*)(ESI+0xc) = (char)in_EAX; *(u8*)(ESI+0xd) = hi`).
  The vendor's programming routine `FUN_00407210` therefore always programs from address 0 after a
  full-chip erase. There is **zero vendor precedent** for the probe's usage, and the one reference that
  does use an offset (SonixFlasherC) warns about it (`flash.c:86-97`) and *skips checksum verification*
  when it is non-zero (`flash.c:182-191`) — the exact opposite of what the probe relies on.
* **If the field is anything other than a byte offset, `0x30000` is the worst possible choice.** Any
  interpretation that multiplies by a power-of-two unit ≥ 64 and truncates to the 18-bit flash address
  space maps `0x30000` to **exactly 0**: `0x30000 = 3·2^16`, so `0x30000 · 2^k (k ≥ 6)` is a multiple of
  `2^22 = 64·2^16`, and the low 18 bits are zero. If the device ignores the field outright, the write
  also starts at 0. Either way the chunk lands on the application's vector table.
* **Measured consequence.** ANDing the probe text (`flash.rs:195-201`, sent as the ASCII tag) into the
  first 64 bytes of the stock image changes 11 bytes, including the vector table:
  initial SP `0x20002B18 → 0x20002B10`, reset PC `0x00007EE5 → 0x00002C60`. The device keeps running —
  a flash write does not reset the core — and fails on the next power cycle. ISP entry is implemented by
  the application firmware (`ISP-ENTRY-SOLVED.md:145-166` documents that the device does not even
  re-enumerate), so a dead application means no way back in. **A stock dump does not help**: you cannot
  program it back without ISP entry.
* **There is no erase and no blank check.** Programming can only clear bits (1→0); writing into
  non-blank cells silently ANDs. The vendor's sequence is erase → *blank check* → enable program
  (`FUN_00407210`), and `flash-probe` skips both. It also never sends `0x07`; not on success
  (`main.rs:617`) and not on the error paths, where `?` at `main.rs:592/601/602` aborts *after* the
  data frame has been written.

Worst realistic outcome, stated plainly: 64 bytes of ASCII ANDed into address 0, a keyboard that works
until the next reboot and then never enumerates again, and no recovery path that does not involve
opening the case.

### CRITICAL-4 — the probe's success test can never pass, so the "de-risking experiment" verifies nothing

`main.rs:608-617`:

```rust
let expected_delta = flash::fw_checksum(&chunk);
if after.wrapping_sub(before) == expected_delta { /* CONFIRMED */ }
```

Writing 64 bytes over erased flash replaces **32 words of `0xFFFF`**, and the device's checksum is the
whole-array sum of 16-bit words — that is what makes `BLANK_CHECKSUM_F290 == 0x0000` true for
262144 bytes of `0xFF` (`chip.h:48`, and the same model reproduces `0xE000`/`0xC000`/`0x8000`/`0x0000`
for F220/F230/F240/F280/F240C). So the real delta is

```
delta = fw_checksum(chunk) - 32*0xFFFF  =  fw_checksum(chunk) - 0xFFE0  =  fw_checksum(chunk) + 0x20
```

Measured with the actual probe payload: `fw_checksum(chunk) = 0x5DA3`, correct delta `0x5DC3`,
the code expects `0x5DA3`. **A perfect write reports `*** INCONCLUSIVE ***`.** The one experiment whose
purpose is to close the last unknown cannot return success under its own documented model, and the
message it prints ("Do NOT proceed to a real flash") invites an operator to retry — each retry being
another unverified flash write.

Two smaller problems in the same command:

* `main.rs:594` prints "enable-program acknowledged" from `check_ack`, which proves nothing (MAJOR-2).
* `main.rs:600-610` reads and discards a frame after the data write and labels its length
  "enable reply len" — the label does not describe the value.

---

## MAJOR findings

### MAJOR-1 — the erase-completion wait and the blank check are missing from the plan

`VENDOR-FLASH-SEQUENCE.md:350-359` and the decompilation of `FUN_00407210`: after sending `0x04` the
vendor does **not** read immediately — it spins 101 iterations (`do { … } while (n < 0x65)`), each with
`local_418 = *(u16*)(table+0x1f)` delay calls (`FUN_004097d0`, a `QueryPerformanceCounter` spin of
`ms`), *then* reads 64 bytes and requires `reply[8..9] == *(u16*)(table+0x25)` (the per-chip blank
checksum) before it will program anything. `main.rs:539-553` goes straight from erase to enable-program,
and there is no blank-check helper anywhere in `flash.rs`.

Because every reply on this device is an **echo** (MAJOR-2), a read taken while the erase is still
running looks identical to a completed erase. Nothing in the current design would notice a program
starting mid-erase. The blank check is the only real completion signal the vendor has, and it is absent.

### MAJOR-2 — `check_ack` cannot fail while the device is answering, so every flash step's error handling is decorative

`flash.rs:215-232` requires `payload[0..3] == <op> AA 55` and `payload[4..7] == 0xFAFAFAFA`.
The device's reply to a feature write is *our own first four bytes echoed back*, plus `FA FA FA FA`,
plus the identity block — measured and tabulated on real hardware in `ISP-ENTRY-SOLVED.md:121-139`,
where even a nonsense payload (`DE AD BE EF`) comes back as
`00 DE AD BE EF FA FA FA FA 20 05 00 00 FF FF 5A 5A`. We always send `<op> AA 55 00`, so the echo
always satisfies the first test, and the device appends the ACK word regardless of what the command was.
`check_ack` therefore reports "the device is in the magic-appended state", not "the command was accepted"
— and it will say so for an unexecuted erase, a refused enable-program, or anything else.

Consequence for the plan: the only real evidence of success is a *data field* — the blank checksum after
erase, and the running checksum after program — and neither is checked:
`check_ack` reads `[4..7]` and never looks at `[8..9]`. Combined with MAJOR-1, the code would erase a chip
and start writing with no evidence that the erase happened.

### MAJOR-3 — `last_chunk_u32` disagrees with the reference for image lengths ≡ 1, 2, 3 (mod 64), and the correct value is undetermined

`flash.rs:118-124` returns `image[len-4..len]`. `flash.c:55-62` records `mem_read_u32_le(buf, bytes_read-4)`
**only when `bytes_read >= 4`**, so for a final partial chunk shorter than four bytes it keeps the
*previous* chunk's value. Modelled both implementations:

| len | `flash.rs` | `flash.c` | |
|---|---|---|---|
| 0,1,2,3 | 0x00000000 | 0x00000000 | agree |
| 4, 5, 63, 64, 68, 127, 128, 132 | — | — | agree |
| **65** | 0xC3BCB5AE | 0xBCB5AEA7 | **differ** |
| **66** | 0xCAC3BCB5 | 0xBCB5AEA7 | **differ** |
| **131** | 0x918A837C | 0x7C756E67 | **differ** |

And a third candidate exists: our own `data_report` zero-pads the final chunk (`flash.rs:183-189`), so if
the device reports the last four bytes of the chunk *it received*, a `len % 64 ∈ {1,2,3}` image yields
`0x00000000`. Three models, three different answers, and this value is compared **after** a full erase.

Nothing validates image length either: `chunk_count` (`flash.rs:104-106`) accepts any size, so
`flash-info` will happily plan a 300 KB file (4692 chunks) for a 4096-chunk part — an over-run whose
behaviour on the device is unknown. Fix: reject (or explicitly pad) any image whose length is not a
multiple of 64, and add tests for 1/3/63/64/65. No test covers any of this today.

### MAJOR-4 — no delays anywhere, and the vendor delays after every single chunk

The vendor inserts a millisecond QPC spin (`FUN_004097d0`, decompiled: `param_1 × QPC_frequency / 1000`
ticks) **after every 64-byte data chunk** inside the program loop of `FUN_00407210`, and a sleep
(`FUN_0040a5d0`, twice in `FUN_0040aac0` and `FUN_0040abc0`) after every `SetFeature`. `VENDOR-FLASH-SEQUENCE.md:284-286`
lists timing between chunks as an open risk. `feature_exchange` (`device.rs:131-138`) writes and reads
back-to-back with no delay at all, and nothing in the printed plan (`main.rs:539-553`) mentions one.

The probe sends one chunk, so this is not a live bug there — but a 4096-chunk burst with no pacing is
the specific thing `isp-burst` exists to worry about, and the failure mode (a dropped or half-programmed
chunk) only becomes visible after the erase. Decide this before implementing the write loop.

### MAJOR-5 — `flash-probe` is destructive with no confirmation, while the source claims otherwise

`flash.rs:31-32` states: *"The commands that reach it require an explicit confirmation flag."*
That is false. `main.rs:561` matches `"flash-probe"` and proceeds: no `--yes`, no prompt, no delay, no
environment check. Verified by reading the whole arm (`main.rs:561-624`) and the argument parser
(`main.rs:126-194`) — there is no confirmation mechanism in the program at all.

It is also **not listed in the usage text** (`main.rs:50-65` omits `flash-info`, `flash-probe` and `raw`),
so a user who reads `--help` has no idea the command exists, let alone that it writes. And `raw`
(`main.rs:321-365`) is an unguarded arbitrary-payload sender: `raw 04AA550000000000000100` is a
full-chip erase, which is documented in this very repository.

---

## MINOR findings

1. **`data_report` is a dead-code trap.** `flash.rs:183-189` returns a **65-byte wire** buffer with the
   report ID already prepended, while `Connection::write_feature` (`device.rs:105-109`) prepends its own.
   It cannot be passed to `write_feature` (type is `&[u8; 64]`), so there is no double-report-ID bug today
   — but the name and shape invite a future caller to prepend a second time via a raw transport. It is
   referenced only from its own tests. The test `data_report_pads_a_short_final_chunk` (`flash.rs:326`)
   also pins zero padding that no reference confirms: `hid_send_payload` sends `[00][bytes_read]`, a
   *short* report, for a partial chunk (`hid_io.c:38-72`).
2. **`probe_chunk`'s tag is 66 bytes** (`flash.rs:197`), so the message is truncated to 64 and reads
   "…the write lande". Cosmetic; the test only checks the `sonix-rs` prefix (`flash.rs:380-383`).
3. **`from_wire`'s structural rule fails on a reply whose payload starts `AA 55`.** `isp.rs:255-265`
   tests offset 1 before offset 2, so for a report-ID-prefixed buffer whose payload begins `AA 55`
   (e.g. the Sonix magic `AA55A55AFF0033CC` sent through `feature_exchange`, or `raw AA55…`) the report ID
   is **not** stripped: `payload[0]` becomes `0x00` and every field is read one byte late. Not reachable
   from the flash path (opcodes `0x02..0x08` occupy payload[0]), and the mirror case — stripping a
   headerless 64-byte payload that has `AA 55` at `[2..4]` — is unreachable because nothing parses a data
   frame through this path (`main.rs:600` reads and discards one). Latent, but it is the same class of
   bug the doc block claims to have fixed, and the regression test `strips_report_id_for_every_opcode_not_just_the_check`
   (`isp.rs:590-615`) never exercises it.
4. **Stale help text contradicting the fixed code.** `main.rs:98-99` and `main.rs:641` still tell the
   operator that success requires `AA 55` at payload `[14..15]` — the exact inverted check that
   `isp.rs:285-297` documents as a bug and `isp.rs:531-543` pins as fixed. `main.rs:74` says the default
   entry magic is `AA55A55AFF0033CC` while `isp.rs:81` sets `AA42895AFF7162CC`.
5. **`security_name` has no `0xC3C3` case** (`flash.rs:74-82`), which is the value the vendor uses for
   chip `0x11`/`0x12` when `sec == 3` (`FUN_00407210`, confirmed). It prints "unknown" for a value that
   is specific to our part. The decode comment at `flash.rs:58-63` omits that branch too.
6. **`--iface` degrades silently.** `main.rs:143-146` does `argv.get(i).and_then(|s| s.parse().ok())`, so
   a missing or unparseable value yields `None` and the transport falls back to auto-selection
   (`device.rs:67-83`) instead of reporting the bad argument.
7. **`sonix-rs checksum` uses the wrong transport for this device.** `main.rs:771-779` goes through
   `Connection::exchange` → `write_payload`/`read_payload` (`device.rs:142-184`), the interrupt/output-report
   dialect, which `VENDOR-FLASH-SEQUENCE.md:27-33` states this keyboard never answers. If it is meant as
   a cross-check of the checksum model (it would be the cheapest way to test the `+0x20` finding),
   it needs the feature-report path used by `flash-probe`. Not executed here.
8. **`flash-info` reads the image file twice** (`main.rs:530` and `main.rs:545-549`); the second read's
   `unwrap_or(0)` fallback is unreachable. Harmless, but the "0 x 64 raw bytes" line can never be a
   useful warning.
9. **Build hygiene:** `main.rs:593` binds `p` and never uses it (`warning: unused variable: p`).

---

## What I verified as correct

* **`erase_payload` (`flash.rs:148-153`) is byte-exact against the vendor.** `FUN_0040ac20` (@`0x0040ac20`)
  writes `04 AA 55 00`, the start page at `[4..5]` from its first argument, the end page at `[8..9]` from
  EAX, and leaves `[6..7]` zero. `flash.rs` does the same, in the same order, in a 64-byte payload.
  The value `256` (`ROM_PAGES_F290`, `flash.rs:46`) matches §1 (`:85-88`), §6 (`:245`) and
  `chip.h:24`/`flash.c:26`. The doc's "pages 0 through 256" is one past a 256-page array, but both
  independent references send `256`, so matching them is right.
* **`enable_program_payload` (`flash.rs:158-163`) is right for offset 0**: offset at `[4..7]`, count at
  `[8..11]`. The vendor writes a u16 at `[8..9]` and zeroes `[10..11]` (`FUN_0040af00`), SonixFlasherC
  writes a u32 at `[8..11]` (`flash.c:136`); for 4096 chunks both produce `00 10 00 00`. Only the probe's
  *non-zero* offset is unsupported — see CRITICAL-3.
* **`pre_erase_payload` (`flash.rs:140-142`)** is `02 AA 55 00` + zeros, matching `FUN_0040abc0` and
  `ISP-ENTRY-PROTOCOL.md:321`. One caveat recorded as undetermined (#5 below): `FUN_0040abc0` zeroes only
  `[6..9]` and leaves `[4..5]` carrying `ctx[8..9]`, so its runtime bytes are not provably all zero.
* **`get_checksum_payload` (`flash.rs:176-178`)** is `06 AA 55 00` with sub-byte 0 = `FUN_0040afb0`.
  (The `[3] = 0x01` variant is a different function, `FUN_0040b110`.)
* **`fw_checksum` (`flash.rs:90-101`) is exactly `checksum_calculate` (`checksum.c:5-24`)**, including the
  odd-length tail, for every length I tested (0,1,2,3,4,5,63,64,65,66,67,68,127,128,129,131,132).
  Whole-image accumulation also equals `flash.c`'s per-chunk accumulation, because 64 is even so pairs
  never straddle a chunk boundary. The tail case the review asked about is handled correctly.
* **`chunk_count` (`flash.rs:104-106`)** equals `(fw_size + REPORT_SIZE - 1)/REPORT_SIZE` (`flash.c:130`),
  including `0 -> 0`.
* **The data frame is exactly `[00][64 raw bytes]`.** `device.rs:105-109` builds `vec![0u8; 65]`, leaves
  `wire[0] = 0` and copies the 64-byte payload into `wire[1..]`; `winhid.rs:317-319` passes `data.len()`
  (= 65) to `HidD_SetFeature`. `main.rs:597` passes the 64-byte `probe_chunk()`. A 65-byte payload is
  impossible — `write_feature` takes `&[u8; REPORT_SIZE]` — and the report ID cannot be duplicated
  because the only builder that prepends one (`data_report`) cannot be passed to it.
* **`flash-info` sends nothing.** `main.rs:526-559` contains no `Connection::open` and no HID call; the
  only I/O is reading the optional image file. The claim holds.
* **The probe's target region really is blank** in the stock dump (measured above), and the firmware
  extent `0x00000..0x17000` is accurate (highest non-`0xFF` below `0x17000` is `0x167F3`).
* **CS0 = `0xFFFF` for our chip** is confirmed by `FUN_00407210` (`sec==0 -> (chip==0xd||chip==0xe) ? 0 : 0xffff`),
  matching `VENDOR-FLASH-SEQUENCE.md:334` and `flash.rs:64`. The *value* is right; only its slot is wrong.
* **`is_isp`/`header_echoes` (`isp.rs:298-338`)** correctly implements `FUN_0040a760`'s single 4-byte echo
  comparison, and the bogus `[14..15]` requirement is genuinely gone.
* **`check_ack`'s field offsets** (payload[0..3] echo, `[4..7]` ACK, `[8..9]` u16 data) match the live
  capture `00 01 AA 55 00 FA FA FA FA 20 05 …` and the protocol write-up. Its *offsets* are right; its
  *discriminating power* is the problem (MAJOR-2).
* **The test suite is green and does not hide the CRITICALs by failing**: `cargo test` = 44 passed, exit 0.
  Two tests do pin wrong behaviour (see next section), but nothing is failing.

### Tests that pass while the code is wrong (item 8)

* `flash.rs:386-394` `probe_offset_is_clear_of_the_firmware` — asserts `PROBE_OFFSET + CHUNK <= 0x3FFFC`
  and hard-codes `FIRMWARE_END = 0x17000`. It encodes the *unproven byte-offset assumption* as a verified
  property and passes regardless of how the device interprets the field. This is the "test that passes
  while the code is wrong" in its purest form: it is green, and the failure mode it cannot see is a write
  at address 0.
* `flash.rs:307-314` `code_option_uses_ffff_for_cs0_on_this_chip` — asserts `p[4..6] == [0xFF, 0xFF]`,
  i.e. pins the swapped slot. It passes, and it makes CRITICAL-1 look verified.
* `isp.rs:571-582` `header_alone_is_sufficient_because_the_vendor_says_so` — asserts that a reply with the
  bare header is a successful ISP response. As a statement about `FUN_0040a760` that is true; as a
  *success test* it is vacuous on this hardware, because the device echoes our own four bytes
  (`ISP-ENTRY-SOLVED.md:121-139`). The test cannot fail while the device is talking, and the inference
  "so the command worked" is what MAJOR-2 is about.
* `flash.rs:290-296` `erase_payload_matches_the_vendor_layout` — asserts `p[10..]` is all zero. The vendor
  writes only `[0..9]` into a reused context buffer and does not zero the tail, so the test asserts more
  than the evidence supports (harmless in practice, but it is not a fact taken from the reference).
* No test exists for `last_chunk_u32` (MAJOR-3), for image-length validation, or for the `AA 55`-first
  reply (MINOR 3).

---

## What I could not determine

1. **What belongs in payload `[4..5]` of the `0x03` write.** The vendor copies `ctx[8..9]` there, and I
   could not resolve what populates `ctx[8..9]` — nor how the 32-bit flash word at `0x3FFFC`
   (`0xAAAA5555` in the dump), the two u16 fields, and the ISP-reported security word (`0x5A5A`) relate.
   Note the vendor never *reads* the code option before erasing either; it writes a value derived from
   the UI/config (`sec`), so "read it first and write it back" is not what the vendor does.
   *Resolved by:* decompiling the writer of `ctx[8..9]` (search for stores to `[reg+8]/[reg+9]` in
   `FUN_00406bb0`'s setup path), or the datasheet's code-option layout.
2. **Whether the chip erase (pages 0..256) destroys a code option written immediately before it.** Both
   the vendor's order and physical sense are in tension (the stock word at `0x3FFFC` is inside the erased
   range). *Resolved by:* the datasheet, or a hardware test on a sacrificial part.
3. **The units of the enable-program offset field.** No vendor precedent (always 0). SonixFlasherC calls
   it an address, uses `0x200` as a safe default for F26X, and refuses to verify the checksum when it is
   non-zero. *Resolved by:* finding any vendor path that sends a non-zero offset — I checked all ten
   callers of the 8-byte variant `FUN_0040ade0`: one passes `8` (`0x0040265d MOV EDX,0x8`, a chip whose
   data frames are 8 bytes), the rest pass `0`; and `FUN_0040af00` (our variant) always zeroes `[4..7]`.
   The `8` is more consistent with a write granularity than with a flash address, but that is inference,
   not proof.
4. **Whether a non-zero-offset program requires a preceding erase**, and whether the device programs 64
   bytes or a whole page/row per frame.
5. **The magnitude of the vendor's delays.** `local_418 = *(u16*)(table+0x1f)` and the argument to
   `FUN_004097d0` were not statically resolved, so I can say *that* there is an erase wait and a per-chunk
   delay, not how long.
6. **Whether the vendor's chunk count for our chip equals ours.** `local_414 = *(u16*)(table+0x2b)` comes
   from a runtime-populated table; if it differs from `chunk_count(image_len)`, the count we send could be
   too small (truncated image) or too large (the device waiting for chunks we never send).
7. **Whether the keyboard's current state is flash-programmable at all.** `ISP-ENTRY-SOLVED.md:145-166`
   says the device never re-enumerated, still reports PID `0x800A`, and answers everything with the same
   block. Nothing I read closes that; every "acknowledgement" in the flash path would look identical in a
   state where no command works.
8. **Whether the erase reply's `[8..9]` is the blank checksum at the moment of reading.** The vendor only
   reads it after the wait, so whether an early read returns a stale or echoed value is unresolved —
   which is precisely why MAJOR-1 matters.
9. **`Flash-probe` was not executed and no HID traffic was generated**, so everything above about device
   behaviour is either measured-and-documented in the repository, decompiled from the vendor binary, or
   computed from the stock dump. Where I have inferred (offset aliasing, the `+0x20` delta), I have said so.

---

## The probe: is it actually low-risk?

**No.** The claim has one true part and three unsupported ones.

*True:* the chosen address is clear of the firmware **if interpreted as a byte offset**. Verified in the
dump: `0x30000..0x30040` is all `0xFF`, the image ends at `0x167F3`, and `0x3FFFC` is 64 KB away.

*Unsupported 1 — the units.* The vendor never sends a non-zero offset in that field; `FUN_0040af00`
zeroes it unconditionally. The probe is the first thing in this project to use it, and its safety depends
entirely on the device reading it as bytes.

*Unsupported 2 — the preconditions.* No erase, no blank check. Programming can only clear bits, so a write
into non-blank cells silently corrupts, and the vendor's blank check exists precisely to know it cannot.
Also, whatever the probe proves about the *data frame*, it proves it in a state (program mode enabled
without an erase, one chunk, no verify) that the real flash never occupies.

*Unsupported 3 — the verdict.* The delta arithmetic is wrong by `0x20`, so a correct write reports
`INCONCLUSIVE`; and the "enable-program acknowledged" line is an echo, not an acknowledgement. The command
therefore cannot return a trustworthy answer either way, while still writing to flash.

*And the downside is not small.* If the offset is ignored, or scaled by anything ≥ 64 and truncated to the
18-bit address space, `0x30000` aliases to exactly `0`. The measured effect of the probe payload at address
0 is 11 corrupted bytes including the reset vector — a keyboard that works until it is unplugged and then
never comes back, with no recovery path because the destroyed application is what implements ISP entry.

If the data frame must be tested, the test has to be redesigned: establish the offset semantics statically
first (see #3 above), make the expected delta `fw_checksum(chunk).wrapping_add(0x20)` — or better, compare
against the reply the *program* step itself returns — add a return-to-user-mode on every exit path
including errors, and put a confirmation flag in front of it. Until then, this command is a coin flip with
a brick on one face, and the coin cannot even tell you which face it landed on.
