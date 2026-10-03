#!/usr/bin/env python3
"""
usb-desc2.py - dump USB descriptors for a device at a known bus/port.

Direct approach: open the parent USB hub by symbolic name and issue
IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION for the specific port. This is the
documented Windows USB hub API and is strictly read-only.

Coordinates come from the registry (LocationInformation = Port_#0012.Hub_#0001)
and DEVPKEY_Device_Parent.

Usage:
    python usb-desc2.py --hub "USB\ROOT_HUB30\5&4087d53&0&0" --port 12
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32")

IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION = 0x00220410
IOCTL_USB_GET_NODE_CONNECTION_NAME = 0x00220414
IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX = 0x00220448

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p]
k32.CreateFileW.restype = ctypes.c_void_p
k32.CloseHandle.argtypes = [ctypes.c_void_p]
k32.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k32.DeviceIoControl.restype = wintypes.BOOL

BUF = 4096


def get_descriptor(hub, port: int, desc_type: int, desc_index: int = 0,
                   langid: int = 0x0409, length: int = 4096):
    """Issue GET_DESCRIPTOR to the device on `port` of hub handle `hub`."""
    setup = (ctypes.c_ubyte * 8)()
    setup[0] = 0x80           # bmRequestType: device-to-host, standard, device
    setup[1] = 0x06           # bRequest: GET_DESCRIPTOR
    if desc_type == 3:        # string: wValue = (langid << 8) | index
        setup[2] = desc_index & 0xFF
        setup[3] = desc_type & 0xFF
        setup[4] = langid & 0xFF
        setup[5] = (langid >> 8) & 0xFF
    else:
        setup[2] = desc_index & 0xFF
        setup[3] = desc_type & 0xFF
        setup[4] = 0
        setup[5] = 0
    setup[6] = length & 0xFF
    setup[7] = (length >> 8) & 0xFF

    total = 8 + 8 + length
    buf = ctypes.create_string_buffer(total)
    # ConnectionIndex (DWORD) = port
    ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(port)), 4)
    # SetupPacket
    ctypes.memmove(ctypes.addressof(buf) + 4, setup, 8)
    ret = wintypes.DWORD(0)
    ok = k32.DeviceIoControl(hub, IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION,
                             buf, total, buf, total, ctypes.byref(ret), None)
    if not ok:
        return None
    # data starts at offset 8 (ConnectionIndex) + 8 (SetupPacket) = 16
    return buf.raw[16:ret.value]


def parse_config(data: bytes) -> dict:
    """Walk a configuration descriptor and its interface/endpoint children."""
    out = {"interfaces": []}
    if not data or len(data) < 9:
        return out
    out["wTotalLength"] = int.from_bytes(data[2:4], "little")
    out["bNumInterfaces"] = data[4]
    out["bConfigurationValue"] = data[5]
    out["bmAttributes"] = f"0x{data[7]:02X}"
    out["bMaxPower_mA"] = data[8] * 2

    i = data[0]
    while i + 2 <= len(data):
        blen = data[i]
        btype = data[i + 1]
        if blen == 0:
            break
        if btype == 4 and i + 9 <= len(data):   # INTERFACE
            iface = {
                "bInterfaceNumber": data[i + 2],
                "bAlternateSetting": data[i + 3],
                "bNumEndpoints": data[i + 4],
                "bInterfaceClass": f"0x{data[i+5]:02X}",
                "bInterfaceSubClass": f"0x{data[i+6]:02X}",
                "bInterfaceProtocol": f"0x{data[i+7]:02X}",
                "iInterface": data[i + 8],
                "endpoints": [],
            }
            out["interfaces"].append(iface)
        elif btype == 5 and i + 7 <= len(data):  # ENDPOINT
            if out["interfaces"]:
                out["interfaces"][-1]["endpoints"].append({
                    "bEndpointAddress": f"0x{data[i+2]:02X}",
                    "bmAttributes": f"0x{data[i+3]:02X}",
                    "wMaxPacketSize": int.from_bytes(data[i + 4:i + 6], "little"),
                    "bInterval": data[i + 6],
                })
        i += blen
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True)
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()

    hub = k32.CreateFileW("\\\\.\\" + args.hub, 0, 0x03, None, 3, 0, None)
    if not hub or hub == INVALID_HANDLE_VALUE:
        print("could not open hub " + chr(92) + chr(92) + "." + chr(92) + args.hub)
        return 1
    try:
        print("=" * 76)
        print(f" USB DESCRIPTOR DUMP - {args.hub} port {args.port}")
        print("=" * 76)

        dev = get_descriptor(hub, args.port, 1, length=18)
        if not dev or len(dev) < 18:
            print("  device descriptor unavailable")
            return 1

        bcdUSB = int.from_bytes(dev[2:4], "little")
        idV = int.from_bytes(dev[8:10], "little")
        idP = int.from_bytes(dev[10:12], "little")
        bcdDev = int.from_bytes(dev[12:14], "little")
        print(f"\n  DEVICE DESCRIPTOR")
        print(f"    bLength            : {dev[0]}")
        print(f"    bDescriptorType    : {dev[1]}")
        print(f"    bcdUSB             : 0x{bcdUSB:04X}  ({bcdUSB >> 8}.{bcdUSB & 0xFF:X})")
        print(f"    bDeviceClass       : 0x{dev[4]:02X}")
        print(f"    bDeviceSubClass    : 0x{dev[5]:02X}")
        print(f"    bDeviceProtocol    : 0x{dev[6]:02X}")
        print(f"    bMaxPacketSize0    : {dev[7]}")
        print(f"    idVendor           : 0x{idV:04X}")
        print(f"    idProduct          : 0x{idP:04X}")
        print(f"    bcdDevice          : 0x{bcdDev:04X}")
        print(f"    iManufacturer      : {dev[14]}")
        print(f"    iProduct           : {dev[15]}")
        print(f"    iSerialNumber      : {dev[16]}")
        print(f"    bNumConfigurations : {dev[17]}")

        cfg = get_descriptor(hub, args.port, 2, length=1024)
        if cfg:
            c = parse_config(cfg)
            print(f"\n  CONFIGURATION DESCRIPTOR")
            print(f"    wTotalLength       : {c.get('wTotalLength')}")
            print(f"    bNumInterfaces     : {c.get('bNumInterfaces')}")
            print(f"    bmAttributes       : {c.get('bmAttributes')}")
            print(f"    bMaxPower          : {c.get('bMaxPower_mA')} mA")
            print(f"\n  INTERFACES ({len(c['interfaces'])})")
            for i in c["interfaces"]:
                print(f"    iface {i['bInterfaceNumber']}  class={i['bInterfaceClass']} "
                      f"sub={i['bInterfaceSubClass']} proto={i['bInterfaceProtocol']} "
                      f"eps={i['bNumEndpoints']} iInterface={i['iInterface']}")
                for e in i["endpoints"]:
                    print(f"        ep {e['bEndpointAddress']} attr={e['bmAttributes']} "
                          f"maxpkt={e['wMaxPacketSize']} interval={e['bInterval']}")

        # string descriptors by index
        print(f"\n  STRING DESCRIPTORS")
        langs = get_descriptor(hub, args.port, 3, desc_index=0, length=255)
        langids = []
        if langs and len(langs) > 2:
            n = (langs[0] - 2) // 2
            for k in range(n):
                langids.append(int.from_bytes(langs[2 + k * 2:4 + k * 2], "little"))
        print(f"    supported langids  : {[hex(x) for x in langids]}")
        lid = langids[0] if langids else 0x0409
        for idx in (dev[14], dev[15], dev[16]):
            if not idx:
                continue
            s = get_descriptor(hub, args.port, 3, desc_index=idx, langid=lid, length=255)
            txt = ""
            if s and len(s) > 2:
                try:
                    txt = s[2:].decode("utf-16-le", "replace").rstrip("\x00")
                except Exception:
                    txt = repr(s)
            print(f"    index {idx:<3}          : {txt!r}")
        return 0
    finally:
        k32.CloseHandle(hub)


if __name__ == "__main__":
    raise SystemExit(main())
