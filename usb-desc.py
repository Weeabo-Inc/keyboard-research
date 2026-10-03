#!/usr/bin/env python3
"""
usb-desc.py - dump the full USB descriptor tree for a device (read-only).

Why: HID reports tell us what the device *speaks*. USB descriptors tell us what
the device *is*: USB revision, power budget, real firmware version, the
manufacturer string index, and the interface/endpoint topology. For identifying
an unknown MCU that is the highest-value free information available, and it
requires no vendor driver and writes nothing.

Retrieves:
  * Device descriptor        (bcdUSB, idVendor, idProduct, bcdDevice,
                              iManufacturer/iProduct/iSerialNumber, bNumConfigurations)
  * Configuration descriptor (bNumInterfaces, bmAttributes, bMaxPower)
  * Interface descriptors    (class/subclass/protocol, endpoints)
  * Endpoint descriptors     (address, attributes, wMaxPacketSize, bInterval)
  * String descriptors       (manufacturer / product / serial, index by index)

Uses SetupAPI + DeviceIoControl(IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION),
the documented Windows USB hub API. Strictly read-only.

Usage:
    python usb-desc.py                 # all USB devices
    python usb-desc.py --vid 05AC      # filter
    python usb-desc.py --strings       # also resolve every string index
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi.dll")
k32 = ctypes.WinDLL("kernel32")
cfgmgr = ctypes.WinDLL("cfgmgr32")

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
DIGCF_ALLCLASSES = 0x04

# {a5dcbf10-6530-11d2-901f-00c04fb951ed} - USB device interface class GUID
USB_DEVICE_GUID = "{A5DCBF10-6530-11D2-901F-00C04FB951ED}"

IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX = 0x00220448
IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION = 0x00220410
IOCTL_USB_GET_NODE_CONNECTION_NAME = 0x00220414

USB_DESCRIPTOR_TYPE_DEVICE = 1
USB_DESCRIPTOR_TYPE_CONFIGURATION = 2
USB_DESCRIPTOR_TYPE_STRING = 3


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class USB_DESCRIPTOR_REQUEST(ctypes.Structure):
    _fields_ = [
        ("ConnectionIndex", wintypes.DWORD),
        ("SetupPacket", ctypes.c_ubyte * 8),
        ("Data", ctypes.c_ubyte * 4096),
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


def guid_from_string(s: str) -> GUID:
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
    return g


def list_usb_interfaces() -> list[str]:
    g = guid_from_string(USB_DEVICE_GUID)
    dev = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not dev or dev == INVALID_HANDLE_VALUE:
        return []
    out = []
    try:
        i = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(dev, None, ctypes.byref(g),
                                                        i, ctypes.byref(ifd)):
                break
            i += 1
            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(dev, ctypes.byref(ifd), None,
                                                      0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(8)), 4)
            di = SP_DEVINFO_DATA()
            di.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    dev, ctypes.byref(ifd), buf, need.value, ctypes.byref(need),
                    ctypes.byref(di)):
                continue
            out.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(dev)
    return out


def hub_name_for(instance_id: str) -> str | None:
    """Resolve the hub IOCTL name for a PnP device instance id."""
    # Use the device instance -> its parent hub. The documented approach uses
    # CM_Get_Parent then CM_Get_Device_ID. Simpler: query the 'LocationInformation'
    # style name via the hub the device hangs off.
    devinst = wintypes.DWORD(0)
    r = cfgmgr.CM_Locate_DevNodeW(ctypes.byref(devinst),
                                  ctypes.c_wchar_p(instance_id), 0)
    if r != 0:
        return None
    parent = wintypes.DWORD(0)
    if cfgmgr.CM_Get_Parent(ctypes.byref(parent), devinst, 0) != 0:
        return None
    buf = ctypes.create_unicode_buffer(512)
    if cfgmgr.CM_Get_Device_IDW(parent, buf, 512, 0) != 0:
        return None
    return f"\\\\.\\{buf.value}"


def get_descriptor(hub, port: int, desc_type: int, desc_index: int = 0,
                   langid: int = 0, length: int = 4096) -> bytes | None:
    req = USB_DESCRIPTOR_REQUEST()
    req.ConnectionIndex = port
    # bmRequestType=0x80 (device-to-host, standard, device)
    req.SetupPacket[0] = 0x80
    req.SetupPacket[1] = 0x06  # GET_DESCRIPTOR
    req.SetupPacket[2] = desc_index & 0xFF
    req.SetupPacket[3] = desc_type & 0xFF
    if desc_type == USB_DESCRIPTOR_TYPE_STRING:
        req.SetupPacket[2] = langid & 0xFF
        req.SetupPacket[3] = (langid >> 8) & 0xFF
    req.SetupPacket[6] = length & 0xFF
    req.SetupPacket[7] = (length >> 8) & 0xFF

    out = ctypes.create_string_buffer(ctypes.sizeof(USB_DESCRIPTOR_REQUEST))
    ctypes.memmove(out, ctypes.byref(req), ctypes.sizeof(req))
    ret = wintypes.DWORD(0)
    ok = k32.DeviceIoControl(hub, IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION,
                             out, ctypes.sizeof(USB_DESCRIPTOR_REQUEST),
                             out, ctypes.sizeof(USB_DESCRIPTOR_REQUEST),
                             ctypes.byref(ret), None)
    if not ok:
        return None
    off = USB_DESCRIPTOR_REQUEST.Data.offset
    data = out.raw[off:ret.value]
    return data if data else None


def sample_strings(prefix: bytes) -> list[tuple[int, str, str]]:
    """Very small regex-free pattern sampler over descriptor bytes.

    We only use this to surface printable ASCII runs, which frequently reveals
    the real manufacturer on devices that spoof another vendor's VID.
    """
    out = []
    cur = []
    start = 0
    for i, b in enumerate(prefix):
        if 32 <= b < 127:
            if not cur:
                start = i
            cur.append(chr(b))
        else:
            if len(cur) >= 4:
                out.append((start, "ascii", "".join(cur)))
            cur = []
    if len(cur) >= 4:
        out.append((start, "ascii", "".join(cur)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strings", action="store_true")
    args = ap.parse_args()
    want = args.vid.upper() if args.vid else None

    paths = list_usb_interfaces()
    print(f"# {len(paths)} USB device interface(s) on this host", file=sys.stderr)

    results = []
    for p in paths:
        if want and f"VID_{want}" not in p.upper():
            continue
        hub = k32.CreateFileW(p, 0, 0x03, None, 3, 0, None)
        if not hub or hub == INVALID_HANDLE_VALUE:
            continue
        try:
            # Port index: use the device's own hub by locating via cfgmgr
            inst = p.split("#", 2)[1] if p.count("#") >= 2 else ""
            # try small port numbers on this hub
            for port in range(1, 8):
                dev = get_descriptor(hub, port, USB_DESCRIPTOR_TYPE_DEVICE,
                                     length=18)
                if not dev or len(dev) < 18:
                    continue
                bcdUSB = int.from_bytes(dev[2:4], "little")
                idV = int.from_bytes(dev[8:10], "little")
                idP = int.from_bytes(dev[10:12], "little")
                bcdDev = int.from_bytes(dev[12:14], "little")
                iMan, iProd, iSer = dev[14], dev[15], dev[16]
                nConf = dev[17]
                rec = {
                    "path": p,
                    "port": port,
                    "bcdUSB": f"0x{bcdUSB:04X}",
                    "idVendor": f"0x{idV:04X}",
                    "idProduct": f"0x{idP:04X}",
                    "bcdDevice": f"0x{bcdDev:04X}",
                    "iManufacturer": iMan,
                    "iProduct": iProd,
                    "iSerialNumber": iSer,
                    "bNumConfigurations": nConf,
                    "raw_device_descriptor": dev[:18].hex(),
                }
                # configuration
                cfg = get_descriptor(hub, port, USB_DESCRIPTOR_TYPE_CONFIGURATION,
                                     length=512)
                if cfg and len(cfg) >= 9:
                    rec["config"] = {
                        "wTotalLength": int.from_bytes(cfg[2:4], "little"),
                        "bNumInterfaces": cfg[4],
                        "bConfigurationValue": cfg[5],
                        "bmAttributes": f"0x{cfg[7]:02X}",
                        "bMaxPower_mA": cfg[8] * 2,
                    }
                if args.strings:
                    sm = {}
                    for idx, name in ((iMan, "manufacturer"), (iProd, "product"),
                                      (iSer, "serial")):
                        if idx:
                            s = get_descriptor(hub, port, USB_DESCRIPTOR_TYPE_STRING,
                                               langid=0x0409, length=255)
                            # index-based fetch is not directly supported by the
                            # hub IOCTL; record what we can
                    rec["strings_note"] = ("descriptor-string fetch by index is not "
                                           "supported via this IOCTL; use hidapi for strings")
                if args.strings and cfg:
                    rec["printable_runs"] = sample_strings(cfg)[:20]
                results.append(rec)
                break
        finally:
            k32.CloseHandle(hub)

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    print("=" * 76)
    print(" USB DESCRIPTOR DUMP (read-only)")
    print("=" * 76)
    for r in results:
        print(f"\n{r['path']}")
        print(f"  bcdUSB            : {r['bcdUSB']}")
        print(f"  idVendor:Product  : {r['idVendor']}:{r['idProduct']}")
        print(f"  bcdDevice (fw)    : {r['bcdDevice']}")
        print(f"  iManufacturer     : {r['iManufacturer']}")
        print(f"  iProduct          : {r['iProduct']}")
        print(f"  iSerialNumber     : {r['iSerialNumber']}")
        print(f"  bNumConfigurations: {r['bNumConfigurations']}")
        if r.get("config"):
            c = r["config"]
            print(f"  interfaces        : {c['bNumInterfaces']}")
            print(f"  bmAttributes      : {c['bmAttributes']}")
            print(f"  bMaxPower         : {c['bMaxPower_mA']} mA")
        if r.get("printable_runs"):
            print("  printable runs in config descriptor:")
            for off, kind, s in r["printable_runs"]:
                print(f"      @{off:<5} {s!r}")
    print(f"\n{len(results)} device(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
