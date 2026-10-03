#!/usr/bin/env python3
"""
via-probe.py - identify a QMK/VIA keyboard over HID, read-only.

WHAT THIS SENDS
  Exactly one feature report per probe: VIA's id_get_protocol_version.
  The device replies with its VIA protocol version. Nothing is configured,
  written, or persisted. This is the equivalent of asking "what protocol do you
  speak?" and nothing more.

WHY FEATURE REPORTS
  On Windows the HID stack restricts normal output/input reports for keyboards
  (they are opened with write-only or read-only access, never both). Feature
  reports go over the control pipe and are the reliable channel for VIA.

PROTOCOL (VIA, as documented in the QMK/VIA ecosystem)
  A 32-byte message:  [0x00, cmd, ...payload]  for interface 0xFF60 (report id 0)
  GET_PROTOCOL = 0x01 -> device returns [cmd, version, ...]

SAFETY
  * Only command 0x01 is ever sent. There is no code path here that writes
    configuration, remaps a key, or resets the device.
  * If the device does not answer, we report that and stop - we do not probe
    blindly with other commands.

Usage:
    python via-probe.py                 # probe 0xFF60 and 0xFF59
    python via-probe.py --usage-page FF60
"""

from __future__ import annotations

import argparse
import sys

try:
    import hid
except ImportError:
    print("hidapi not installed: python -m pip install hidapi", file=sys.stderr)
    raise SystemExit(2)

VID = 0x05AC
PID = 0x024F

# VIA command ids (subset - only the read-only ones are defined here on purpose)
VIA_GET_PROTOCOL = 0x01

REPORT_ID = 0x00
MSG_LEN = 32


def describe(d) -> str:
    return (f"iface={d.get('interface_number')} "
            f"usage_page=0x{d.get('usage_page', 0):04X} "
            f"usage=0x{d.get('usage', 0):04X}")


def probe(usage_page: int, usage: int) -> dict:
    """Send the single read-only GET_PROTOCOL request and read the reply."""
    infos = [d for d in hid.enumerate(VID, PID)
             if d.get("usage_page") == usage_page and d.get("usage") == usage]
    if not infos:
        return {"ok": False, "error": f"no interface with usage_page=0x{usage_page:04X} usage=0x{usage:04X}"}

    info = infos[0]
    dev = hid.device()
    try:
        dev.open_path(info["path"])
    except Exception as e:
        return {"ok": False, "error": f"open failed: {e}", "interface": describe(info)}

    try:
        # Build the request: 32-byte message starting with the command byte.
        # For a device using report ID 0, hidapi passes the buffer straight
        # through - it does NOT inject an extra report-id byte. Prepending one
        # ourselves would shift the command byte and the device would ignore it.
        buf = bytearray(MSG_LEN)
        buf[0] = VIA_GET_PROTOCOL

        errors = []
        sent = None
        try:
            sent = dev.send_feature_report(bytes(buf))
        except Exception as e:
            errors.append(f"send_feature_report: {e}")
            # Fall back to an interrupt OUT report for devices that route the
            # vendor protocol over interrupt endpoints rather than feature reports.
            try:
                sent = dev.write(bytes(buf))
                errors.append("(fell back to interrupt write)")
            except Exception as e2:
                errors.append(f"write: {e2}")

        if sent is None:
            return {"ok": False, "error": "; ".join(errors),
                    "interface": describe(info)}

        # Read replies. Try feature first, then interrupt IN. Retry a few times:
        # the device may need a moment to prepare the response.
        reply = b""
        read_errors = []
        for attempt in range(4):
            try:
                r = dev.get_feature_report(0x00, MSG_LEN + 1)
                if r:
                    reply = bytes(r)
                    break
            except Exception as e:
                read_errors.append(f"feature[{attempt}]: {e}")
            try:
                r = dev.read(MSG_LEN + 1, timeout_ms=300)
                if r:
                    reply = bytes(r)
                    break
            except Exception as e:
                read_errors.append(f"read[{attempt}]: {e}")

        # Look for the echoed command byte (0x01) followed by a version byte.
        version = None
        if len(reply) >= 2:
            for i in range(len(reply) - 1):
                if reply[i] == VIA_GET_PROTOCOL:
                    version = reply[i + 1]
                    break

        return {
            "ok": True,
            "interface": describe(info),
            "bytes_sent": sent,
            "notes": errors,
            "reply_len": len(reply),
            "reply_hex": reply.hex(),
            "read_errors": read_errors[:4],
            "via_protocol_version": version,
        }
    finally:
        try:
            dev.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--usage-page", default=None,
                    help="probe only this usage page, e.g. FF60")
    args = ap.parse_args()

    targets = [(0xFF60, 0x61), (0xFF59, 0x61)]
    if args.usage_page:
        want = int(args.usage_page, 16)
        targets = [t for t in targets if t[0] == want]

    print("=" * 72)
    print(" VIA PROTOCOL PROBE  (read-only: one GET_PROTOCOL feature report)")
    print("=" * 72)
    print()
    print(" Devices:")
    for d in hid.enumerate(VID, PID):
        print(f"   {describe(d)}  {d.get('product_string')!r}")
    print()

    for up, us in targets:
        print(f"--- probing usage_page=0x{up:04X} usage=0x{us:04X} ---")
        r = probe(up, us)
        if not r.get("ok"):
            print(f"    NOT CONFIRMED: {r.get('error')}")
            if r.get("interface"):
                print(f"    ({r['interface']})")
        else:
            print(f"    interface   : {r['interface']}")
            print(f"    sent        : {r['bytes_sent']} bytes")
            print(f"    reply len   : {r['reply_len']}")
            print(f"    reply hex   : {r['reply_hex']}")
            if r["via_protocol_version"] is not None:
                v = r["via_protocol_version"]
                print(f"    *** VIA PROTOCOL VERSION {v} ***")
                print("    -> This IS a VIA/QMK-compatible keyboard.")
            else:
                print("    reply did not contain a recognisable GET_PROTOCOL response")
                print("    -> could be a different vendor protocol on this interface")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
