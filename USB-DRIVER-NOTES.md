# SN32F290 USB Driver

A Rust `usb_device::bus::UsbBus` implementation for the Sonix SN32F290's
full-speed USB device controller, for the AULA S98Pro keyboard.

Code lives in `s98rs/usb/`. Nothing existing was modified: `s98rs/pac/`,
`s98rs/fw/`, `s98rs/tools/` and `sonix/` are all read-only inputs to this work,
and no hardware was touched.

| Path | What it is |
|---|---|
| `s98rs/usb/src/regs.rs` | Register layer: offsets, bit definitions, FIFO window, PAC cross-check |
| `s98rs/usb/src/pma.rs` | Packet-memory allocator (pure logic, fully unit-tested) |
| `s98rs/usb/src/bus.rs` | `UsbBus` impl, endpoint-0 state machine, interrupt handler |
| `s98rs/usb/src/lib.rs` | Crate root and the `SET_ADDRESS_QUIRK` explanation |
| `s98rs/usb/src/tests.rs` | 29 host tests driving the real driver against a mock register file |
| `s98rs/usb/examples/hid_keyboard.rs` | HID keyboard: enumerates and sends a keypress |
| `s98rs/usb/tools/svd_dump.py` | Dumps SVD register/field data (wrote it to find the PAC bug) |
| `s98rs/usb/tools/check_vectors.py` | Asserts IRQ 1 in a linked ELF points at *our* handler |

Build results, all reproduced below in "What compiles and what is untested":

```
cargo build --release --target thumbv6m-none-eabi                                   # exit 0
cargo test  --target x86_64-pc-windows-msvc                    # 29 + 1 passed, 0 failed
cargo build --release --target thumbv6m-none-eabi \
      --example hid_keyboard --features hid-example                                 # exit 0
```

---

## Which usb-device version and why

**Targeted version: `usb-device` 0.3.2, pinned as `version = "=0.3.2"`.**

Two reasons, one of which is a hard constraint:

1. **0.3.x is the first series that can build for Cortex-M0 at all.**
   `thumbv6m-none-eabi` has no `LDREX`/`STREX`, so `target_has_atomic = "32"` is
   absent and `core::sync::atomic::AtomicPtr` does not exist. `UsbBusAllocator`
   uses an `AtomicPtr` to hand endpoint handles a reference to the bus, so
   `usb-device` 0.2.x cannot compile for this target. 0.3.x replaced core atomics
   with `portable-atomic`, which is what makes it possible.

   Worth recording precisely *why* it then works, because "portable-atomic fixed
   it" is not the real answer and would mislead anyone debugging a related
   problem later. `cargo tree -e features` shows `portable-atomic v1.15.0`
   resolved with **no features enabled** - no `fallback`, no `critical-section`,
   no `unsafe-assume-single-core`:

   ```
   ├── usb-device v0.3.2
   │   ├── portable-atomic v1.15.0
   ```

   That is fine because ARMv6-M *does* provide atomic word load/store (a plain
   aligned `LDR`/`STR` is atomic), and `usb-device` only ever performs `load` and
   `store` on its `AtomicPtr` - it is written once in `freeze()` before any
   sharing and only read thereafter. It never needs compare-and-swap. So no
   software fallback is pulled in and none is needed. Had `usb-device` used a
   CAS anywhere, this would not link.

2. **0.3.2 is the only version in the local registry cache**
   (`~/.cargo/registry/cache/.../usb-device-0.3.2.crate`), so the pinned build
   works offline.

The trait surface implemented is that of 0.3.2's `src/bus.rs`: `alloc_ep`,
`enable`, `reset`, `set_device_address`, `write`, `read`, `set_stalled`,
`is_stalled`, `suspend`, `resume`, `poll`, plus the
`QUIRK_SET_ADDRESS_BEFORE_STATUS` const and the default `force_reset`
(left as `Unsupported`).

`usbd-hid` 0.10.2 is present in the cache and declares `usb-device = "0.3.0"`, so
it is compatible - but it depends on `usbd-hid-macros`, which is **not** in the
cache, so it cannot be built offline. The example therefore contains a
hand-written HID class instead; see "What compiles and what is untested".

---

## Register map used (with SVD citations)

`SN_USB` base `0x4005C000`, IRQ 1, from `research/SN32F290.patched.svd`.
Packet RAM is at base + `0x100`.

| Offset | Name | SVD description |
|---|---|---|
| `0x00` | `INTEN` | USB Interrupt Enable Register |
| `0x04` | `INSTS` | USB Interrupt Event Status Register (RO) |
| `0x08` | `INSTSC` | USB Interrupt Event Status Clear Register (W1C) |
| `0x0C` | `ADDR` | USB Device Address Register |
| `0x10` | `CFG` | USB Configuration Register |
| `0x14` | `SGCTL` | USB Signal Control Register |
| `0x18 + 4n` | `EPnCTL` | Endpoint `n` Control, `n` = 0..=6 |
| `0x3C` | `EPTOGGLE` | Endpoint Data Toggle Register *(unused by this driver)* |
| `0x48 + 4(n-1)` | `EPnBUFOS` | Endpoint `n` Buffer Offset, `n` = 1..=6 |
| `0x60` | `FRMNO` | Frame Number (used by the example for 1 ms timing) |
| `0x64` / `0x6C` | `PHYPRM` / `PHYPRM2` | PHY parameters |
| `0x78` / `0x7C` / `0x80` | `RWADDR` / `RWDATA` / `RWSTATUS` | FIFO window 1 |
| `0x84` / `0x88` / `0x8C` | `RWADDR2` / `RWDATA2` / `RWSTATUS2` | FIFO window 2 |

Register block ends at `0x90`. All of the above are declared in
`s98rs/usb/src/regs.rs` as `OFF_*` constants and asserted against the generated
PAC by `regs::verify_pac_layout()`, which the example calls at startup and halts
on if it fails.

### The PAC's USB field accessors are unusable

This is the single most important thing to know before writing SN32F290 USB
code, and it is not obvious from the PAC.

The vendor SVD's `bitWidth` elements inside `SN_USB` are **corrupt**: every field
was emitted with `bitWidth == bitOffset + 1`. svd2rust faithfully turned that into
nonsense. From `pac/src/lib.rs`:

```rust
// EP0CTL, a single-bit field at bit 27, is given a 28-bit mask:
pub fn out_stall_en(&self) -> OutStallEnR {
    OutStallEnR::new((self.bits >> 27) & 0x0fff_ffff)
}
// ENDP_CNT is masked to 7 bits; ChibiOS says the field is 9 bits wide:
pub fn endp_cnt(&self) -> EndpCntR { EndpCntR::new((self.bits & 0x7f) as u8) }
```

Every field in every `SN_USB` register is affected, including the writers, so
neither reading nor writing through them is safe. Two of them are correct by
accident (`EP1_DIR` at bit 0, `ENDP_EN` at bit 31, both width 1).

The PAC is **not** wrong about the register *layout*: `sn_usb::RegisterBlock`
places every register at the SVD's offset, sizes to `0x90`, and that layout
matches ChibiOS's `sn32_usb_t` struct exactly. So this crate uses the PAC for the
base address and for a compile-time size/alignment cross-check, and does all bit
manipulation with raw `u32` values and named constants.

`tools/svd_dump.py SN_USB` prints the corruption field by field.

### Bit definitions, and the three-source cross-check

Every bit used is confirmed by three independent sources:

1. the vendor SVD's `bitOffset` values (reliable; its `bitWidth` is not),
2. `research/ChibiOS-Contrib` `os/hal/ports/SN32/LLD/SN32F2xx/USB/`
   (`usbhw.h`, `sn32_usb.h`, `hal_usb_lld.c`) - Apache-2.0, and this is the
   *SonixQMK* fork (`git remote` = `github.com/SonixQMK/ChibiOS-Contrib`), which
   is the driver QMK ships for these keyboards,
3. the decompiled vendor stock image (see below).

`INSTS` / `INSTSC`, all agreeing exactly across all three:

| Bit | Name | ChibiOS mask |
|---|---|---|
| `1<<(n-1)`, n=1..6 | `EPn_NAK` | `mskEPn_NAK(ep)` |
| `1<<(8+n-1)`, n=1..6 | `EPn_ACK` | `mskEPn_ACK(ep)` |
| 17 | `ERR_TIMEOUT` | `mskERR_TIMEOUT` |
| 18 | `ERR_SETUP` | `mskERR_SETUP` |
| 19 | `EP0_OUT_STALL` | `mskEP0_OUT_STALL` |
| 20 | `EP0_IN_STALL` | `mskEP0_IN_STALL` |
| 21 | `EP0_OUT` | `mskEP0_OUT` |
| 22 | `EP0_IN` | `mskEP0_IN` |
| 23 | `EP0_SETUP` | `mskEP0_SETUP` |
| 24 | `EP0_PRESETUP` | `mskEP0_PRESETUP` |
| 25 | `BUS_WAKEUP` | `mskBUS_WAKEUP` |
| 26 | `USB_SOF` | `mskUSB_SOF` |
| 29 | `BUS_RESUME` | `mskBUS_RESUME` |
| 30 | `BUS_SUSPEND` | `mskBUS_SUSPEND` |
| 31 | `BUS_RESET` | `mskBUS_RESET` |

`CFG`: `EPn_DIR` at `1<<(n-1)` (0 = IN only, 1 = OUT only), `DIS_PDEN` 26,
`ESD_EN` 27, `SIE_EN` 28, `DPPU_EN` 29, `PHY_EN` 30, `VREG33_EN` 31.

`INTEN`: `EPn_NAK_EN` `1<<(n-1)`, `EPN_ACK_EN` `1<<6`, `BUSWK_IE` 28, `USB_IE` 29,
`USB_SOF_IE` 30, `BUS_IE` 31.

`EPnCTL`: `ENDP_CNT` bits 8:0, `EP0_OUT_STALL_EN` 27, `EP0_IN_STALL_EN` 28,
`ENDP_STATE` 30:29 (0 = NAK, 1 = ACK, 3 = STALL), `ENDP_EN` 31.

`ADDR`: `UADDR` bits 6:0. `SGCTL`: `BUS_DPDN_STATE` 1:0, `BUS_DRVEN` 2.
`RWSTATUS`: `W_STATUS` 0, `R_STATUS` 1.

**Two documented disagreements between sources, both resolved explicitly:**

* **`ENDP_CNT` width.** The SVD says 7 bits; ChibiOS masks `0x1FF` (9 bits).
  Full-speed packets are at most 64 bytes, so both are large enough and the
  difference cannot change behaviour for any legal value. This driver masks 9
  bits: that is safe if the SVD is right and necessary if ChibiOS is right,
  whereas the reverse is not.
* **`EPN_ACK_EN` width.** The SVD's `bitOffset` is 6 but its declared span to
  the next field is 22 bits, so the SVD does not actually say how wide it is.
  ChibiOS writes it as a single bit (`0x1 << 6` for a 6-endpoint part) and the
  vendor firmware's own `INTEN` value has exactly bit 6 set. Both agree: one bit.

Also note `USB_RAM_SIZE = 512`, which comes from ChibiOS
(`SN32_USB_PMA_SIZE 512` for SN32F290) and is corroborated by its allocator
comment ("the first 64 bytes are reserved... the effective available RAM for
endpoint buffers is just 192/448 bytes"). This is an **assumption**, not
something I measured; see "What I could not determine".

### The vendor stock image agrees, independently

Decompiled from `keyboard/stock/S98Pro_SN32F290_stock.bin` (Ghidra program
`S98Pro_SN32F290_stock.bin`, ARM Cortex-M0). The USB IRQ vector is flash word 17
(byte `0x44`) = `0x000008E9`, i.e. IRQ 1, matching the SVD and ChibiOS's
`SN32_USB_HANDLER = Vector44` (ChibiOS names vectors by byte offset; `0x44` is
entry 17).

The functions that matter:

* **`FUN_000012c8`** - the reset handler, and it is ChibiOS's `usb_lld_reset`
  almost line for line:
  ```
  USB->INSTSC = 0xFFFFFFFF;
  USB->ADDR   = 0;
  for (ep = 1; ep < 7; ep++) EPCTL[ep] = 0;
  ```
  (ChibiOS: `INSTSC = UINT32_MAX`, `ADDR = 0`, and `usb_lld_disable_endpoints`
  zeroing `EPCTL[1..6]`.)

* **`FUN_00001146(ep)`** → `EPCTL[ep] = 0` for `ep < 7`, which independently
  confirms the `EPCTL` array base `0x18`, stride 4, and range `EP0..EP6`.

* **`FUN_0000116a(ep, n)`** compiles to `EPCTL[ep] = n - 0x60000000`, i.e.
  `0xA0000000 | n` once truncated to 32 bits. `0xA0000000` is exactly
  `ENDP_EN | ENDP_STATE_ACK`, so this is ChibiOS's `EPCTL_SET_STAT_ACK(ep, n)`
  bit for bit. `FUN_00001158(ep)` is the NAK form (`0x80000000`) and
  `FUN_00000e10` writes `0xE0000000`, the STALL form
  (`ENDP_EN | ENDP_STATE_STALL`). All four states confirmed from a fourth
  source.

* **`FUN_00000fe6`** ends with `USB->INTEN = 0xB0000040`, which is
  `BUS_IE | USB_IE | BUSWK_IE | EPN_ACK_EN` - exactly the enable set ChibiOS
  uses, and exactly what this driver's `enable()` produces. Note the vendor does
  *not* enable `USB_SOF_IE` by default, and neither does ChibiOS unless a SOF
  callback is registered.

* **Suspend**: `FUN_00000194` and `FUN_00000754` both do
  `USB->CFG &= 0xB7FFFFFF`. `0xB7FFFFFF` clears exactly bits 30 and 27, i.e.
  `PHY_EN | ESD_EN` - ChibiOS's `CFG &= ~(mskESD_EN|mskPHY_EN)`. **Resume**
  (`FUN_00001310`) is `CFG |= 0x48000000`, the same two bits, then
  `INSTSC = 0x02000000` to clear `BUS_WAKEUP`. This driver does the same.

* **FIFO protocol**, confirmed from the vendor's own helpers:
  * `FUN_00001228(addr, data)`: `RWADDR = addr; RWDATA = data; RWSTATUS = 1;
    while (RWSTATUS & 1);`
  * `FUN_0000120c(addr)`: `RWADDR = addr; RWSTATUS = 2; while (RWSTATUS & 2);
    return RWDATA;`
  * `FUN_000011c2(addr, data)` and `FUN_00001180(addr)` are the same two
    operations through port 2.
  These are byte-for-byte the sequences in ChibiOS's `sn32_usb_write_fifo` /
  `sn32_usb_read_fifo`.

* **Endpoint 0's buffer** is at packet RAM offset 0: `FUN_000014a2`, the EP0 IN
  handler, writes 16 words (64 bytes) with `RWADDR = i*4` and then sets
  `EPCTL[0] = ACK | remaining`. That is why the PMA allocator starts at 64.

One thing the vendor does that this driver deliberately does **not**: when the
EP0 data stage finishes, `FUN_000014a2` sets `EPCTL[0] |= 0x10000000` (bit 28,
which the SVD calls `IN_STALL_EN`) if the last packet was short. ChibiOS only
sets that bit in response to an `EP0_IN_STALL` interrupt. The two uses of the bit
are incompatible, so I do not touch it and neither should you without a
datasheet.

---

## Endpoint / FIFO strategy

### Endpoints 1..=6 are unidirectional, and that constrains everything

`CFG.EPn_DIR` selects whether endpoint number `n` answers IN tokens or OUT
tokens. There is **one** `EPnCTL` and **one** `EPnBUFOS` per endpoint *number*,
not per direction. So unlike almost every other USB device controller, you cannot
have EP1-IN and EP1-OUT at the same time.

The SVD states this outright ("EP1 only handshakes to IN token packet" /
"...to OUT token packet"), and `EP1BUFOS` having no sibling `EP1BUFOS_IN` is the
structural reason.

`alloc_ep` enforces it: a number already allocated is refused with
`InvalidEndpoint` regardless of which direction is asking. Endpoint 0 is exempt -
it is bidirectional in hardware and does not consume a packet-memory buffer.

This is the most likely source of a porting surprise. A `usb-device` class that
expects `bulk(64)` OUT and `bulk(64)` IN to land on the same number will still
work, because `usb-device` passes `None` and takes whatever number it is given -
but a class that explicitly asks for, say, `EndpointAddress(0x01)` and
`EndpointAddress(0x81)` will find the second request refused.

### No endpoint-type configuration exists

`EPnCTL` has enable, handshake state and byte count, and nothing else. ChibiOS's
`usb_lld_init_endpoint` switches on the endpoint type and executes an empty body
in every arm. Control, bulk and interrupt endpoints are handled identically by
this hardware.

Isochronous is refused at allocation time with `Unsupported`, rather than
accepted and quietly mishandled. There is no evidence the SIE does periodic
scheduling, and silently accepting an allocation it cannot service would be
worse than an error at start-up.

### Packet memory

512 bytes at `0x4005C100`. Offset 0..63 belongs to endpoint 0 (both its SETUP
packet and its data buffer live there - they never coexist). The allocator is a
bump allocator starting at 64.

**Deliberate deviation from ChibiOS:** `usb_pm_alloc` rounds each allocation up
to an even number of bytes (`(size + 1) & ~1`); this driver rounds to a multiple
of 4. The FIFO is only reachable through 32-bit word accesses at 4-byte-aligned
addresses (`RWADDR`'s field is a *word* address in bits 9:2), so an allocation
ending on an odd 2-byte boundary would put the next buffer somewhere it cannot be
read correctly. Every legal full-speed packet size (8, 16, 32, 64) is already a
multiple of 4, so for real endpoints the two policies produce identical layouts;
the difference only appears for sizes this controller cannot be asked to use.

### FIFO access: ports 1 and 2, and which to use

There are two independent windows onto the same packet RAM. Both can read and
write; the vendor uses both for both. The driver uses ChibiOS's split, which is
the field-proven one:

* **port 1** (`RWADDR`/`RWDATA`/`RWSTATUS`) for all endpoint data, both
  directions;
* **port 2** (`RWADDR2`/`RWDATA2`/`RWSTATUS2`) for reading the SETUP packet,
  and nothing else.

The reason is defensive: if the SIE holds port 1 during the setup phase, reading
through port 2 avoids contending with it. I have no evidence that it does, but
the split costs nothing and matches the driver that ships on real SN32F290
keyboards.

Both addresses are **byte offsets written verbatim**. `EPBUFOS`'s `OFFSET` field
and `RWADDR`'s `RWADDR` field both sit at `bitOffset 2`, so the register value is
the word offset shifted left by two - which is the byte offset. ChibiOS and the
vendor both write byte offsets, and both field layouts confirm it.

### Out-of-line data

Packet data is read from and written to the packet RAM lazily, at the moment
`usb-device` asks for it, rather than being copied into SRAM by the interrupt
handler. That saves 64 bytes of static RAM per endpoint, which matters on a
32 KB part, and it is safe because the SIE returns the handshake state to NAK
after every transaction (ChibiOS: *"useless mcu resets it anyways"*), so nothing
overwrites a buffer before it is read.

The one exception is the SETUP packet, which *is* copied in the interrupt
handler - see below.

### The endpoint-0 hazard that shapes the design

Packet RAM offset 0 holds **both** the 8-byte SETUP packet and endpoint 0's
64-byte data buffer. They cannot coexist.

So the interrupt handler copies the SETUP packet into processor RAM and leaves
endpoint 0 **NAKing**. The receiver is armed only later, by `UsbBus::read`, once
the SETUP bytes have been handed to `usb-device`.

This is worth spelling out because the obvious optimisation - read the direction
bit from the SETUP packet and arm the receiver immediately - is wrong. The host
can deliver the first OUT data packet within microseconds of the SETUP; it would
land on top of the SETUP packet that has not finished being read. NAKing for the
few microseconds it takes the main loop to pick the packet up is exactly what NAK
is for, and it costs nothing.

The driver *does* read `wLength` and the direction bit from the SETUP packet at
that later point, to decide whether to arm the receiver for a data stage. That is
not parsing the request - `usb-device` owns that - it is only working out which
way to point the endpoint.

### Arming the status stage

For a control-IN transfer, once the data stage is finished the next token from
the host is the status stage: an OUT token. `usb-device` does not tell the bus
when the data stage ends directly, so the driver tracks it:

* `write(0x80, ..)` records bytes sent and `wLength` from the SETUP;
* a packet shorter than 64 bytes, or reaching `wLength`, marks the data stage
  done;
* the next `EP0_IN` interrupt then arms `EPCTL[0] = ACK | 0`.

`ACK | 0` is right for both directions of status token: it accepts a zero-length
OUT, and it answers an IN token with a zero-length packet, which is what a
control-OUT status stage is. The control-OUT case is driven by `usb-device`
itself through `ControlPipe::accept_out` -> `ep_in.write(&[])`.

I considered re-arming `ACK | 0` on every `EP0_IN` (which is what the vendor's
`FUN_000014a2` does when its remaining count hits zero) and rejected it: between
that re-arm and `usb-device`'s next `write` there is a window in which a host IN
token would receive an unrequested zero-length packet. Tracking the length
closes the window.

**`set_stalled(0x00, false)` leaves endpoint 0 NAKing, never ACKing.**
`ControlPipe::handle_setup` calls `unstall` immediately after reading the SETUP
packet, so arming a zero-length ACK there would open the same window. NAK is
always safe; the endpoint is armed at the points where the driver knows what the
next token means.

### Interrupt handler

`Sn32UsbBus::interrupt_handler()` is the ISR body; `InterruptHandler` wraps a
`&'static Sn32UsbBus` in the shape the other drivers use. It does the
register-level work only and latches events for `poll` - it deliberately does not
run `usb-device`'s state machine, which belongs in thread context.

`poll` semantics per `usb-device`'s contract:
* `ep_setup` and `ep_out` are level flags, cleared by `read`;
* `ep_in_complete` is an edge notification, reported once and cleared on report.
  `UsbDevice::poll` deliberately drops it when it coincides with a SETUP or OUT
  event for the same endpoint, so clearing on report is correct.

The endpoint-0 sub-handlers are independent rather than an `else if` chain (which
is what ChibiOS uses). A single `INSTS` read can legitimately have more than one
endpoint-0 bit set, and dropping one would lose a packet. Each branch clears only
the bit it consumed, so anything left over re-triggers the interrupt.

Bus reset re-arms the PHY, returns to address 0 and disables every endpoint
immediately - the device must stop answering at the old address at once - while
the full re-initialisation happens in `UsbBus::reset`, which `usb-device` calls
as soon as `poll` reports the reset. The host waits roughly 10 ms after a bus
reset before enumerating, so there is ample time.

### Concurrency

`Sn32UsbBus` is `Sync` via `unsafe impl Sync`, with the argument written out at
the impl: every `&self` method that touches state does so inside
`critical::free`, which on the target masks all configurable-priority interrupts
- and the USB IRQ is one of those - so the ISR cannot be running while a
`&mut State` exists. The only other mutator is `&mut self`, in `alloc_ep` and
`enable`, which cannot overlap with a `&self` borrow. This is the same argument
`stm32-usbd` and `synopsys-usb-otg` make.

`Cell` and `RefCell` were not options: neither is `Sync`, so neither can appear
in a type that has to implement `UsbBus`.

---

## What compiles and what is untested

### Compiles and runs — verified

```
$ cargo build --release --target thumbv6m-none-eabi
    Finished `release` profile [optimized + debuginfo] target(s) in 0.45s
$ echo $?
0
```

No warnings from `sn32f290-usb` (the PAC itself emits 36 pre-existing ones).

```
$ cargo test --target x86_64-pc-windows-msvc
running 29 tests
...
test result: ok. 29 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out

running 1 test
test result: ok. 1 passed; 0 failed; 2 ignored; 0 measured; 0 filtered out
```

```
$ cargo build --release --target thumbv6m-none-eabi \
      --example hid_keyboard --features hid-example
    Finished `release` profile [optimized + debuginfo] target(s) in 1.16s
```

And two offline checks over the linked image:

```
$ python tools/check_vectors.py target/thumbv6m-none-eabi/release/examples/hid_keyboard
symbol       : USB = 0x000000FD
IRQ 1        : vector word 17 (byte 0x44) = 0x000000FD
DefaultHandler = 0x00001CB9
OK  IRQ 1 -> USB (0x000000FC)

$ python ../tools/elf2fw.py .../hid_keyboard /tmp/hid.bin
   ok  SP 0x20002800 in SRAM, 8-byte aligned
   ok  reset -> 0x000000C0 (Thumb bit set, in flash)
   ok  ELF entry agrees (0x000000C1)
   ok  6/15 exception vectors present and valid
   ok  real content ends at 0x2894 (10,388 bytes)
ALL CHECKS PASSED
```

`check_vectors.py` exists because a Cortex-M interrupt handler that fails to
override `PROVIDE(USB = DefaultHandler)` does not fail to build *or* to link -
the image is perfectly valid and the interrupt silently goes nowhere. On a board
whose only recovery route is a bootloader handshake, that is worth catching on
the host. It confirms IRQ 1 points at our handler and not at `DefaultHandler`.

### The host tests, and what they actually prove

29 tests drive the **real** `Sn32UsbBus` - its `UsbBus` impl and its interrupt
handler - against a memory-backed register file instead of the peripheral. The
mock is not a stub: it models the FIFO window's handshake (write `RWADDR`, write
`RWDATA`, write `RWSTATUS`, poll `RWSTATUS`, read `RWDATA`), because otherwise
the driver's poll loops would spin forever and nothing could be tested at all.

They cover: allocation respecting the unidirectional-endpoint constraint and
running out gracefully; packet RAM being handed out without overlap; the SETUP
packet being copied out *before* endpoint 0 is armed; the direction bit in `CFG`
matching the allocated direction; `ENDP`/`EPBUFOS` values; `INTEN` ending up at
`0xB0000040`; `CFG` suspend/resume matching the vendor's `0xB7FFFFFF` /
`0x48000000` masks; the full endpoint-0 IN sequence including the status stage and
the once-only `ep_in_complete` notification; bus reset clearing the address and
re-enabling endpoints; interrupt IN round trips; and the error paths.

**They prove the driver's bookkeeping and register traffic, and nothing about the
silicon.** The mock stores what it is given. It does not auto-NAK after a
transaction, does not raise interrupts, and does not move data on a bus. A test
passing means the driver asked the hardware for the right thing - not that the
hardware agreed.

### Not tested at all

* **Nothing has run on the SN32F290.** No hardware was touched, nothing was
  flashed. This is the honest headline.
* **Enumeration is unverified.** The descriptor set, the SET_ADDRESS sequence,
  the GET_DESCRIPTOR flow - all reasoned from `usb-device`'s source and the
  SN32's documented behaviour, none observed.
* **The HID example has never sent a keypress.**
* **The interrupt handler has never fired.** Its register writes are exercised by
  the host tests; the fact that the hardware raises those bits in response to
  those tokens is not.
* **Isochronous is refused, so it is not merely untested - it is unsupported.**
* **`EPTOGGLE` is never written.** The SVD, ChibiOS and the vendor disagree on
  whether a 1 or a 0 in a bit position means "reset this toggle", and the driver
  does not need to touch it because the SIE manages toggles and clears them on
  bus reset. If a real endpoint ever mis-toggles, that register is the place to
  look.

---

## What I could not determine

Ordered by how likely each is to bite.

### 1. Does the controller raise `EP0_IN` for a zero-length IN packet?

**This is the one that could stop enumeration.**

`usb-device` follows USB 2.0 §9.4.6: for `SET_ADDRESS` it calls
`set_device_address` only *after* the status stage completes, which it learns from
`handle_in_complete` returning `completed == true`. That requires the controller
to send a zero-length IN packet and then to raise `INSTS.EP0_IN` for it.

If it does not, `set_device_address` is never called, the device never answers at
its new address, and enumeration fails right after `SET_ADDRESS` - the host gets a
device descriptor and then gives up and asks again.

Evidence that it works: both the vendor firmware and ChibiOS arm a zero-length
endpoint-0 transfer (`EPCTL[0] = ACK | 0`) and then wait for the completion, which
is only sensible if the completion arrives. That is strong but it is inference,
not observation.

**Mitigation, already built in.** The crate has a cargo feature for exactly this:

```
cargo build --release --target thumbv6m-none-eabi \
      --features quirk-set-address-before-status
```

That sets `QUIRK_SET_ADDRESS_BEFORE_STATUS = true`, making `usb-device` program
the address before the status stage instead of after, so enumeration no longer
depends on the unverified behaviour. It is a small deviation from the
specification - the address is supposed to take effect after the status stage -
in exchange for removing the dependency. **If enumeration stalls at
`SET_ADDRESS`, turn this on first.** It is off by default because the
spec-correct path is the one to try first.

### 2. Is the packet RAM really 512 bytes?

The whole packet-memory strategy rests on ChibiOS's `SN32_USB_PMA_SIZE 512` for
SN32F290 and its comment that only 448 bytes are usable after endpoint 0's
reservation. I did not measure it and the SVD's `addressBlock` size of `0x2000`
says nothing useful.

If 512 is wrong, the failure mode is an endpoint whose buffer overlaps another's,
which shows up as corrupted transfers rather than a crash. Six endpoints of
64 bytes need only 384 bytes, so a HID keyboard has margin; a composite device
with more or larger endpoints does not.

### 3. What bit 28 of `EP0CTL` actually does

The SVD calls it `IN_STALL_EN` ("Enable EP0 IN STALL handshake") and ChibiOS sets
it only in response to an `EP0_IN_STALL` interrupt. The vendor sets it after a
short packet ends a control-IN data stage. Those are not the same thing. The
driver touches neither, so this is a gap in understanding rather than a risk -
but it means an EP0 stall behaves in a way I cannot fully predict.

### 4. `PHYPRM` and `PHYPRM2` are magic numbers

`0x80000000` and `0x00004004`, copied verbatim from ChibiOS's `usb_lld_start`.
The SVD names the fields (`PHY_PARAM`, `PHY_PS`) and documents no encoding. If the
PHY does not come up, these are two numbers nobody currently understands.

### 5. The clock, and whether USB needs 48 MHz

The internal RC oscillator frequency is not established, and I did not need to
establish it: **neither ChibiOS nor the vendor configures a PLL or any clock
divider in the USB bring-up path**. `usb_lld_start` only enables the AHB clock
gate (`SYS1.AHBCLKEN[4] = USBCLKEN`, SVD `SN_SYS1.AHBCLKEN.USBCLKEN` at
`bitOffset 4`, and ChibiOS's `sys1EnableUSB()` is `sys1EnableAHB(0x1 << 4)`).
There is no `48` anywhere in the SN32F290 USB code.

So this driver assumes the USB block has its own clock source and does not
require the core to run at 48 MHz. If that is wrong, it will show up as the
device never appearing on the bus at all. ChibiOS's SN32F290 default clock is
a 16 MHz crystal through a PLL with `PLL_MSEL = 2`, which is 32 MHz, not 48 - so
if USB did need exactly 48 MHz, QMK's keyboards could not work either.

One consequence for the example: with no reliable clock, it derives its 1 ms tick
from `FRMNO`, the USB frame number register, which increments once per
millisecond while the bus is active. That needs no frequency assumption at all.

### 6. Whether `usb-device`'s ZLP behaviour matches this controller

When a control-IN data stage ends on a full 64-byte packet, `usb-device` sends an
extra zero-length packet. The driver arms `ACK | 0` after the data stage, which
happens to serve as the status stage in both directions, so I believe the two
compose correctly - but this is reasoning from `usb-device`'s source, not
observation, and it is the kind of interaction that shows up only on hardware.

---

## References and licences

Our project is MIT. Nothing GPL was copied; nothing was transliterated.

| Source | Licence | How it was used |
|---|---|---|
| `research/SN32F290.patched.svd` | vendor document | Register offsets and `bitOffset` values. Ground truth. |
| `SonixQMK/ChibiOS-Contrib`, `os/hal/ports/SN32/LLD/SN32F2xx/USB/` | **Apache-2.0** | Read for understanding. Bit positions, FIFO protocol, bring-up sequence. MIT-compatible, but the Rust here is written fresh rather than transliterated. |
| `SonixQMK/ChibiOS-Contrib`, `os/hal/ports/SN32/SN32F290/` | Apache-2.0 | `sn32_sys1.h` for the clock gate; `sn32_registry.h` for the vector-numbering convention. |
| Vendor stock image `keyboard/stock/S98Pro_SN32F290_stock.bin` | our own dump | Decompiled in Ghidra for independent confirmation of register semantics. |
| `qmk_firmware` | **GPL-2.0** | **Read only, for orientation.** Its board config (`platforms/chibios/boards/SN_SN32F290/configs/mcuconf.h`) confirms the ChibiOS defaults are used unmodified. Nothing was copied. Its vendored `lib/chibios-contrib` is an empty submodule in this checkout and could not be consulted. |
| `usb-device` 0.3.2 | MIT | The trait implemented. |
| `stm32-usbd`, `synopsys-usb-otg` | MIT | Referenced for the shape of a `UsbBus` implementation and the `Sync` argument. **Neither is available in the local registry**, so they were not read - the design follows `usb-device`'s own documentation and source. |

Provenance note: the local `ChibiOS-Contrib` checkout is genuinely the SonixQMK
fork (`git remote -v` → `https://github.com/SonixQMK/ChibiOS-Contrib.git`,
HEAD `5bed8690`, Jan 2026), which is the tree QMK builds these keyboards from.
That matters: it is the *shipping* driver for this silicon, not a generic port.

### Tools written

* `s98rs/usb/tools/svd_dump.py` - dumps a peripheral's registers and fields from
  the SVD, and flags every field whose declared `bitWidth` disagrees with the
  span to the next field. This is what found the PAC corruption.
* `s98rs/usb/tools/check_vectors.py` - resolves a symbol from `.symtab` and
  asserts a chosen IRQ vector in a linked ELF points at it, distinguishing "wrong
  address" from "fell through to `DefaultHandler`".
