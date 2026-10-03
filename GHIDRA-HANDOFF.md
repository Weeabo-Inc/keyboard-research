# Ghidra Handoff — S98Pro ISP Tool Reverse Engineering

**Goal:** find the USB mechanism this tool uses to put the AULA S98Pro keyboard into
**ISP mode** — the one blocker left before we can flash.

**Why it matters:** every software entry route we tried is closed (see §4). The vendor's
own tool *works*, so the mechanism is in this binary.

**Target:** `<HOME>\Downloads\S98PRO Firemware.exe` (2,216,960 bytes, PE32, MSVC C++)
**Copy staged at:** `<REPO_ROOT>\keyboard\ghidra\stage\S98PRO_Firmware.exe`

---

## 1. What the tool IS (verified)

```
LegalCopyright : Copyright (c) 2008- Sonix Technology Co., LTD. All Rights Reserved.
FileVersion    : 2.1.2.6
Title (config) : HFD ISP Tool
```

**Sonix's own ISP flasher** — the silicon vendor's tool, not a rebadge.

Its `Settings\UISettings.ini` (extracted from PE resource type 0xA) contains:

```ini
CheckDeviceCmd=AA55A55AFF0033CC     ; the ISP entry command, per the ini
CheckBootLoaderID=1                 ; checks for a bootloader VID/PID
CurVersion=0x0109                   ; expected firmware version
ISPTitle=HFD ISP Tool
[SpecialPID0] VID=0x0C45 PID=0x8009 ChipName=SN32F290 FWName=SN32F290.hex CodeSecurity=1
[SpecialPID1] VID=0x0C45 PID=0x8801 ChipName=SN32F290 FWName=SN32F290.hex CodeSecurity=1
```

Sonix's own `UISettings_user_guide.txt` annotates the same field differently:
```ini
CheckDeviceCmd=AA55A55AFF0033CC      ;ISP command setting.
```

---

## 2. PE layout (verified)

| Section | file range | notes |
|---|---|---|
| `.text` | `0x400 .. 0x159600` | tool code |
| `.rdata` | `0x159600 .. 0x1A6E00` | **strings/logic here** |
| `.data` | `0x1A6E00 .. 0x1B1000` | |
| `.rsrc` | `0x1B1000 .. 0x21D400` | **firmware + payloads here** |

---

## 3. High-value addresses found so far

### Strings (in `.rdata` — these are the tool's LOGIC, xref them)

| Offset | String |
|---|---|
| `0x15C50C` | `Check if connected device is SONiX ISP` |
| `0x15C574` | `Check ISP Password` |
| `0x15C590` / `0x15C5A8` | `Check Chip Name` |
| `0x15C5DC` / `0x15C5F8` | `Check Build Time` |
| `0x15E1BC` | `Find Device` |
| `0x15E214` | `SNX_Find_HID` |
| `0x15C49C` | `Get the firmware version` |
| `0x15C2EC` | `This firmware version is not supported` |

**`SNX_Find_HID` and `Find Device` are the device-enumeration routines — start there.**
**`Check if connected device is SONiX ISP` is the ISP probe — that's the target.**

### Magic values

| Offset | Value | Note |
|---|---|---|
| `0x1BB164` | `AA 42 89 5A FF 71 62 CC` | = `0x5A8942AA` + `0xCC6271FF` — **the HFD reboot magic, in `.rsrc`** |
| `0x1B6816` | `FF 00 33 CC` | **FALSE POSITIVE** — this is a colour palette table, not protocol |

**`0x5AA555AA` (the `sonix` magic) appears NOWHERE in the binary.** Only the HFD pair.
So this build targets the HFD variant. The ini's `CheckDeviceCmd` looks like the Sonix
magic when read LE, but those bytes are not present — **worth resolving**, since the ini
value is what the tool loads at runtime.

### Embedded payloads (in `.rsrc`)

| Offset | Size | What |
|---|---|---|
| RVA `0x1C564C` | 262,144 | a 256 KB firmware image (vector table at start) |
| RVA `0x20567C` | 145,091 | **a ZIP** → `Settings/FWFiles/SN32F290.hex` (the real firmware) |

### ARM code inside `.rsrc`

Around `0x1BB144` there is **ARM Thumb code**:
```
0x1BB144  01 69 01 22 52 04 11 43 01 61 70 47   ← 70 47 = BX LR
0x1BB154  14 00 00 20  00 C0 05 40  38 00 00 20  ← SRAM ptrs (0x20000014, 0x4005C000)
0x1BB164  AA 42 89 5A FF 71 62 CC                ← the HFD magic as a data constant
0x1BB174  40 00 00 20 B9 00 00 20 BB 00 00 20    ← more vector-table data
```
**This looks like an embedded ISP bootloader / payload that the tool sends.** Worth
identifying: if the magic is a *data constant inside a payload the tool uploads*, then
the entry sequence is "upload payload → magic → device jumps".

---

## 4. Hardware state — what we already tried and RULED OUT

The keyboard is `VID_0C45:PID_800A` (app mode), on a **direct root-hub port** (`Port_#0014`),
8 HID interfaces:

| Interface | usage_page | in/out/feature | accepts feature reports? |
|---|---|---|---|
| MI_00 | `0x0001` | 9 / 2 / 0 | ❌ Incorrect function |
| MI_01 | `0x000C` etc | 3,2,16,5,4 / 0 / 0 | ❌ Incorrect function |
| MI_02 | `0xFF68` | 65 / 4097 / 0 | ❌ Incorrect function |
| **MI_03** | **`0xFF13`** | **65 / 65 / 65** | ✅ **the only one** |

**`MI_03` (`0xFF13`) is the command channel** — measured, not assumed.

### Measured results (all real, from hardware)

| Attempt | Result |
|---|---|
| `vcmd 0x21` (GET_VERSION) | reply `00 21 00 00 00 ...` — echoes cmd, **no payload** |
| `raw AA55A55AFF0033CC` (ini cmd) | echo `00 AA 55 A5 FF FF 00 33 CC` — **recognised, not obeyed** |
| `raw AA42895AFF7162CC` (guide cmd) | all zeros — **not recognised** |
| `reboot --type sonix` magic | no transition |
| `reboot --type hfd` magic | no transition |
| single-byte `0x20,0x22,0x23,0x24,0x25,0x26,0x27,0x28,0x29,0x2A,0x2B,0x30,0x40,0xFF` | all `00 <cmd> 00 00 00 00` |
| `FN+ESC` + replug (live USB watch) | device vanished @25s, **returned as `0x800A` in <150 ms** |
| Any ISP PID (`7900/7040/7160/7010/7120/7140/8009/8801`) | **never appeared** |

**Conclusion: the app firmware ACKs but never hands off to the bootloader.** The entry
mechanism must be something we have not replicated.

---

## 5. The tool DOES start — needed its Settings folder

It exits silently if `Settings\` is missing. A working directory was reconstructed at:

```
<REPO_ROOT>\keyboard\sonix-tool\
  S98PRO_Firmware.exe
  mfc140u.dll  msvcp140.dll  vcruntime140.dll
  Settings\
    UISettings.ini
    UISettings_user_guide.txt
    FWFiles\SN32F290.hex
    Image\*.bmp
```
Launching from there gives the **"HFD ISP Tool"** window with a Start button.
**It did not appear to detect the keyboard** (status bar blank), but the user reports
**"this tool works — it manages to interact with the keyboard"**, so that reading may be
wrong. **Verify: does the tool detect the device? Does clicking Start do anything?**

---

## 6. What to ask Ghidra (via MCP)

1. **Xref `SNX_Find_HID` and `Find Device`** → decompile → what VID/PIDs does it match?
   Does it match `0x800A`, or only `0x8009`/`0x8801`? Does it use a range?
2. **Xref `Check if connected device is SONiX ISP`** → decompile → **what exactly does it
   send, and on which HID interface / report type?**
3. **Find what reads `CheckDeviceCmd` from the ini** → decompile → how are those 8 bytes
   delivered: feature report? output report? which interface? is there a second command?
4. **Find the code referencing `0x1BB164`** (the HFD magic in `.rsrc`) → is that payload
   uploaded before the magic is sent?
5. **Look for a HID write call taking 65 bytes** (`HidD_SetFeature` / `WriteFile`) and trace
   back to the ISP entry path.
6. **Check for a delay / retry loop** after the magic — maybe the device needs the magic
   *and* a subsequent poll, which our tool does not do.

---

## 7. If Ghidra is blocked — the definitive alternative

**Capture the USB traffic.** USBPcap + Wireshark, run the tool, click Start, and read the
exact packets off the wire. That answers §6 directly without any decompilation.

Install: `winget install --id WiresharkFoundation.Wireshark` (includes USBPcap).

---

## 8. Assets already in place

| Path | What |
|---|---|
| `keyboard/stock/S98Pro_SN32F290_stock.bin` | **stock firmware**, raw 256 KB |
| `keyboard/stock/S98Pro_SN32F290_stock.hex` | same, Intel HEX (5,766 records, **0 checksum errors**) |
| `keyboard/S98PRO-FIRMWARE.md` | how the firmware was recovered |
| `sonix/` | our own Rust ISP tool (`sonix-rs`, 13 tests green) |
| `sonix/PROTOCOL.md` | full ISP protocol reference |
| `sonix/extract-fw.py`, `sonix/hex2bin.py` | firmware extraction + HEX validation |
| `keyboard/ghidra/` | project dir, `scripts/DumpIsp.java`, staged binary |
| `keyboard/SONIX-RESEARCH.md` | broader Sonix MCU research |

**We hold the stock firmware — so a failed flash is recoverable.** That removes the
one-way-door risk that made this cautious.
