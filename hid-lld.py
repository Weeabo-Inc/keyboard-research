#!/usr/bin/env python3
"""
hid-lld.py - low-level HID interface probe for the "S98Pro Dongle" keyboard.

READ-ONLY. Opens each HID collection (preferring zero-access query handles when a
read handle is refused) and retrieves:

  * HidP_GetCaps   - usage page/usage, report lengths, link-collection count
  * HidP_GetLinkCollectionNodes - the collection tree, which shows how a vendor
    interface is structured
  * IOCTL_HID_GET_REPORT_DESCRIPTOR - the raw report descriptor, i.e. the
    device's own description of its protocol

Why: two vendor-defined interfaces (0xFF60 and 0xFF59) are the interesting
surface. 0xFF60 with 33-byte bidirectional reports is the QMK/VIA raw-HID
convention. The report descriptor confirms or refutes that.

Safety: nothing is written to the device. No output/feature reports are sent.
On an input device a malformed write can leave you unable to type, so this tool
deliberately has no write path at all.

Usage:
    python hid-lld.py                 # all HID collections
    python hid-lld.py --vid 05ac      # filter
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from ctypes import wintypes

hid = ctypes.WinDLL("hid.dll")
setupapi = ctypes.WinDLL("setupapi.dll")
k32 = ctypes.WinDLL("kernel32")

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B01C0
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
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


class HIDP_LINK_COLLECTION_NODE(ctypes.Structure):
    _fields_ = [
        ("LinkUsage", wintypes.USHORT), ("LinkUsagePage", wintypes.USHORT),
        ("Parent", wintypes.USHORT), ("NumberOfChildren", wintypes.USHORT),
        ("NextSibling", wintypes.USHORT), ("FirstChild", wintypes.USHORT),
        ("CollectionType", wintypes.ULONG), ("IsAlias", wintypes.ULONG),
        ("UserContext", ctypes.c_void_p),
    ]


k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p]
k32.CreateFileW.restype = ctypes.c_void_p
k32.CloseHandle.argtypes = [ctypes.c_void_p]
k32.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k32.DeviceIoControl.restype = wintypes.BOOL

hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = wintypes.LONG
hid.HidP_GetLinkCollectionNodes.argtypes = [ctypes.POINTER(HIDP_LINK_COLLECTION_NODE),
                                            ctypes.POINTER(wintypes.ULONG),
                                            ctypes.c_void_p]
hid.HidP_GetLinkCollectionNodes.restype = wintypes.LONG

setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                          ctypes.c_void_p, wintypes.DWORD]
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]


def open_hid(path: str):
    """Open a HID collection, trying progressively weaker access rights."""
    for access, label in ((0x80000000, "read"),
                          (0x80000000 | 0x40000000, "read+write"),
                          (0x40000000, "write"),
                          (0, "query-only")):
        h = k32.CreateFileW(path, access, 0x03, None, 3, 0, None)
        if h and h != INVALID_HANDLE_VALUE:
            return h, label
    return None, None


def read_report_descriptor(h) -> bytes | None:
    buf = ctypes.create_string_buffer(4096)
    ret = wintypes.DWORD(0)
    ok = k32.DeviceIoControl(h, IOCTL_HID_GET_REPORT_DESCRIPTOR, None, 0,
                             buf, 4096, ctypes.byref(ret), None)
    if ok and ret.value:
        return buf.raw[:ret.value]
    return None


def get_caps(h):
    pp = ctypes.c_void_p()
    if not hid.HidD_GetPreparsedData(h, ctypes.byref(pp)) or not pp:
        return None, None
    try:
        caps = HIDP_CAPS()
        rc = hid.HidP_GetCaps(pp, ctypes.byref(caps))
        if rc < 0:
            return None, None
        nodes = None
        n = caps.NumberLinkCollectionNodes
        if n:
            arr = (HIDP_LINK_COLLECTION_NODE * n)()
            ln = wintypes.ULONG(n)
            if hid.HidP_GetLinkCollectionNodes(arr, ctypes.byref(ln), pp) >= 0:
                nodes = [{
                    # ctypes maps USHORT to a SIGNED short, so mask to 16 bits
                    # before formatting or f"{v:X4}" raises on negative values.
                    "usage": f"0x{arr[i].LinkUsage & 0xFFFF:04X}",
                    "usage_page": f"0x{arr[i].LinkUsagePage & 0xFFFF:04X}",
                    "parent": arr[i].Parent & 0xFFFF,
                    "children": arr[i].NumberOfChildren & 0xFFFF,
                    "collection_type": arr[i].CollectionType,
                } for i in range(min(ln.value, n))]
        return caps, nodes
    finally:
        hid.HidD_FreePreparsedData(pp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    want = int(args.vid, 16) if args.vid else None

    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        print("SetupDiGetClassDevs failed", file=sys.stderr)
        return 1

    out = []
    try:
        i = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid),
                                                        i, ctypes.byref(iface)):
                break
            i += 1
            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(iface), None,
                                                      0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize is the size of the FIXED
            # part of the struct: 8 on 64-bit (DWORD + pointer-aligned WCHAR),
            # 6 on 32-bit. Using sizeof(SP_DEVICE_INTERFACE_DATA) (16) makes
            # SetupDiGetDeviceInterfaceDetailW fail silently and enumeration
            # yields nothing.
            ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(8)), 4)
            di = SP_DEVINFO_DATA()
            di.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(iface), buf, need.value, ctypes.byref(need),
                    ctypes.byref(di)):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)

            # cheap VID filter from the path so we do not open unrelated devices
            low = path.lower()
            if want is not None and f"vid_{want:04x}" not in low:
                continue

            h, mode = open_hid(path)
            if not h:
                out.append({"path": path, "error": "could not open any access mode"})
                continue
            try:
                caps, nodes = get_caps(h)
                rd = read_report_descriptor(h)
                rec = {
                    "path": path,
                    "open_mode": mode,
                    "usage_page": f"0x{caps.UsagePage:04X}" if caps else None,
                    "usage": f"0x{caps.Usage:04X}" if caps else None,
                    "in_len": caps.InputReportByteLength if caps else None,
                    "out_len": caps.OutputReportByteLength if caps else None,
                    "feature_len": caps.FeatureReportByteLength if caps else None,
                    "link_collections": nodes,
                    "report_descriptor_hex": rd.hex() if rd else None,
                    "report_descriptor_len": len(rd) if rd else 0,
                }
                out.append(rec)
            finally:
                k32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print("=" * 76)
    print(" HID LOW-LEVEL PROBE (read-only, no writes)")
    print("=" * 76)
    for r in out:
        print(f"\n[{(r['path'].split('#')[-2] if '#' in r['path'] else r['path'])}]")
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  open mode      : {r['open_mode']}")
        print(f"  usage page     : {r['usage_page']}   usage {r['usage']}")
        print(f"  report lengths : in={r['in_len']} out={r['out_len']} feature={r['feature_len']}")
        if r["link_collections"]:
            print(f"  collections    : {len(r['link_collections'])}")
            for n in r["link_collections"][:8]:
                print(f"      page={n['usage_page']} usage={n['usage']} type={n['collection_type']} children={n['children']}")
        if r["report_descriptor_hex"]:
            hx = r["report_descriptor_hex"]
            print(f"  REPORT DESC    : {r['report_descriptor_len']} bytes")
            for j in range(0, len(hx), 64):
                print(f"      {hx[j:j+64]}")
        else:
            print("  REPORT DESC    : unavailable via IOCTL")
    print(f"\n{len(out)} collection(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
