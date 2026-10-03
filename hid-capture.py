#!/usr/bin/env python3
"""
hid-capture.py - read USB HID report descriptors and device strings on Windows.

READ-ONLY. This opens HID collections for query only (share mode read+write is
requested but no writes are issued) and asks Windows for:
  * the raw HID REPORT DESCRIPTOR per collection (the thing that tells us what a
    vendor-defined interface actually speaks),
  * the HIDD_ATTRIBUTES (VID/PID/version),
  * the manufacturer / product / serial strings,
  * the preparsed data summary (usage pages, report lengths).

Why this matters for the keyboard research:
  The device presents four HID interfaces, two of them vendor-defined with no
  standard driver. A HID report descriptor is the device's own description of its
  protocol - if a vendor interface speaks a firmware-update or config protocol,
  the report descriptor is usually where it shows. It is readable from userland,
  needs no driver swap, and cannot modify the device.

Safety note: we deliberately do NOT write to the device. On an input device, a
botched write can leave you unable to type.

Usage:
    python hid-capture.py              # all HID devices
    python hid-capture.py --vid 05AC   # filter by vendor id
    python hid-capture.py --json       # machine-readable
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from ctypes import wintypes

# --- Windows HID API -------------------------------------------------------

hid = ctypes.WinDLL("hid.dll")
setupapi = ctypes.WinDLL("setupapi.dll")
kernel32 = ctypes.WinDLL("kernel32.dll")

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

HIDD_ATTRIBUTES_SIZE = 12


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(wintypes.ULONG)),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(wintypes.ULONG)),
    ]


hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetSerialNumberString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
hid.HidP_GetCaps.restype = wintypes.LONG
hid.HidD_GetIndexedString.argtypes = [wintypes.HANDLE, wintypes.ULONG, wintypes.LPVOID, wintypes.ULONG]

setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                        wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                 ctypes.POINTER(GUID), wintypes.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
kernel32.DeviceIoControl.restype = wintypes.BOOL

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10


def wide_to_str(buf) -> str:
    try:
        s = ctypes.wstring_at(buf)
        return s.rstrip("\x00")
    except Exception:
        return ""


def get_string(fn, handle) -> str:
    buf = ctypes.create_unicode_buffer(512)
    try:
        if fn(handle, buf, ctypes.sizeof(buf)):
            return buf.value
    except Exception:
        pass
    return ""


def read_report_descriptor(path: str, handle) -> bytes | None:
    """
    Retrieve the raw HID report descriptor.

    HidD_GetReportDescriptor is not exported by every Windows build, so the
    reliable route is:
      1. read ReportDescriptorLength from the registry key the HID stack already
         created for this device (avoids guessing the buffer size),
      2. issue IOCTL_HID_GET_REPORT_DESCRIPTOR to fetch the bytes.

    This is strictly read-only.
    """
    # --- attempt the direct HidD export first (cheap if present) ---
    try:
        fn = hid.HidD_GetReportDescriptor
        fn.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
        fn.restype = wintypes.BOOL
        buf = ctypes.create_string_buffer(4096)
        if fn(handle, buf, ctypes.sizeof(buf)):
            raw = buf.raw
            n = int.from_bytes(raw[:4], "little")
            if 0 < n <= len(raw) - 4:
                return raw[4:4 + n]
            return raw.rstrip(b"\x00")
    except AttributeError:
        pass

    # --- IOCTL route ---
    # Work out the length from the registry.
    length = _rd_length_from_registry(path)
    if not length or length <= 0 or length > 8192:
        length = 1024

    IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B01C0
    buf = ctypes.create_string_buffer(length)
    returned = wintypes.DWORD(0)
    ok = kernel32.DeviceIoControl(
        handle, IOCTL_HID_GET_REPORT_DESCRIPTOR,
        None, 0,
        buf, length,
        ctypes.byref(returned), None,
    )
    if ok and returned.value > 0:
        return buf.raw[:returned.value]

    # Retry with a larger buffer in case the registry value was stale.
    big = ctypes.create_string_buffer(4096)
    returned2 = wintypes.DWORD(0)
    ok2 = kernel32.DeviceIoControl(
        handle, IOCTL_HID_GET_REPORT_DESCRIPTOR,
        None, 0, big, 4096, ctypes.byref(returned2), None,
    )
    if ok2 and returned2.value > 0:
        return big.raw[:returned2.value]
    return None


def _rd_length_from_registry(path: str) -> int | None:
    """
    Parse the HID device path and read ReportDescriptorLength from
    HKLM\\SYSTEM\\CurrentControlSet\\Enum\\HID\\<vid>_<pid>&mi_xx\\<instance>.
    """
    try:
        import re
        import winreg
        m = re.search(r"hid#(vid_[0-9a-f]{4}&pid_[0-9a-f]{4}(?:&mi_[0-9a-f]{2})?(?:&col[0-9a-f]{2})?)#([^#]+)#",
                      path, re.IGNORECASE)
        if not m:
            return None
        dev, instance = m.group(1), m.group(2)
        key_path = rf"SYSTEM\CurrentControlSet\Enum\HID\{dev}\{instance}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
            v, _ = winreg.QueryValueEx(k, "ReportDescriptorLength")
            return int(v)
    except Exception:
        return None


# HID usage pages of interest
USAGE_PAGES = {
    0x01: "Generic Desktop",
    0x02: "Simulation",
    0x06: "Generic Device Controls",
    0x07: "Keyboard/Keypad",
    0x08: "LED",
    0x09: "Button",
    0x0C: "Consumer",
    0x0D: "Digitizer",
    0xFF00: "VENDOR-DEFINED 0xFF00",
    0xFF01: "VENDOR-DEFINED 0xFF01",
    0xFF02: "VENDOR-DEFINED 0xFF02",
    # QMK/VIA/Vial raw HID convention
    0xFF60: "VENDOR-DEFINED 0xFF60 (QMK/VIA raw HID)",
    0xFF59: "VENDOR-DEFINED 0xFF59",
    0xFFFF: "VENDOR-DEFINED 0xFFFF",
}


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid", help="filter by vendor id, e.g. 05AC")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    want_vid = int(args.vid, 16) if args.vid else None

    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))

    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE_VALUE or not hdev:
        print("SetupDiGetClassDevs failed", file=sys.stderr)
        return 1

    results = []
    try:
        idx = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid),
                                                        idx, ctypes.byref(iface)):
                break
            idx += 1

            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(iface), None, 0, ctypes.byref(need), None)
            if need.value == 0:
                continue
            buf = ctypes.create_string_buffer(need.value)
            # cbSize field: DWORD on 64-bit, must be 8
            ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(8)), 4)
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(iface), buf, need.value, ctypes.byref(need),
                    ctypes.byref(devinfo)):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)

            h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                     FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                     OPEN_EXISTING, 0, None)
            if h == INVALID_HANDLE_VALUE or not h:
                # try read-only before giving up
                h = kernel32.CreateFileW(path, GENERIC_READ,
                                         FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                         OPEN_EXISTING, 0, None)
                if h == INVALID_HANDLE_VALUE or not h:
                    continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                if not hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                    continue
                if want_vid is not None and attrs.VendorID != want_vid:
                    continue

                caps = HIDP_CAPS()
                usage_page = None
                usage = None
                lengths = None
                pp = ctypes.c_void_p()
                if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)) and pp:
                    try:
                        if hid.HidP_GetCaps(pp, ctypes.byref(caps)) >= 0:
                            usage_page = caps.UsagePage
                            usage = caps.Usage
                            lengths = {
                                "in": caps.InputReportByteLength,
                                "out": caps.OutputReportByteLength,
                                "feature": caps.FeatureReportByteLength,
                            }
                    finally:
                        hid.HidD_FreePreparsedData(pp)

                rd = read_report_descriptor(path, h)

                rec = {
                    "path": path,
                    "vid": f"0x{attrs.VendorID:04X}",
                    "pid": f"0x{attrs.ProductID:04X}",
                    "version": f"0x{attrs.VersionNumber:04X}",
                    "manufacturer": get_string(hid.HidD_GetManufacturerString, h),
                    "product": get_string(hid.HidD_GetProductString, h),
                    "serial": get_string(hid.HidD_GetSerialNumberString, h),
                    "usage_page": usage_page,
                    "usage_page_name": USAGE_PAGES.get(usage_page, f"0x{usage_page:04X}" if usage_page else None),
                    "usage": usage,
                    "report_lengths": lengths,
                    "vendor_defined": bool(usage_page and usage_page >= 0xFF00),
                    "report_descriptor_hex": rd.hex() if rd else None,
                    "report_descriptor_len": len(rd) if rd else 0,
                }
                results.append(rec)
            finally:
                kernel32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("=" * 74)
        print(" HID REPORT DESCRIPTOR CAPTURE (read-only)")
        print("=" * 74)
        for r in results:
            flag = "  <<< VENDOR-DEFINED" if r["vendor_defined"] else ""
            print(f"\n{r['vid']}:{r['pid']}  rev {r['version']}{flag}")
            print(f"  manufacturer : {r['manufacturer']!r}")
            print(f"  product      : {r['product']!r}")
            print(f"  serial       : {r['serial']!r}")
            print(f"  usage page   : {r['usage_page_name']}  (usage {r['usage']})")
            if r["report_lengths"]:
                L = r["report_lengths"]
                print(f"  report len   : in={L['in']} out={L['out']} feature={L['feature']}")
            if r["report_descriptor_hex"]:
                hx = r["report_descriptor_hex"]
                print(f"  report desc  : {r['report_descriptor_len']} bytes")
                for i in range(0, len(hx), 64):
                    print(f"      {hx[i:i+64]}")
            else:
                print("  report desc  : (not retrievable via HidD_GetReportDescriptor)")
            print(f"  path         : {r['path']}")
        print(f"\n{len(results)} HID collection(s) inspected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
