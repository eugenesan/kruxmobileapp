#!/usr/bin/env python3
"""Does the Android base32 shim match the firmware's, byte for byte?

src/krux/bbqr.py and baseconv.py `import base32`, a MicroPython built-in that
does not exist on CPython and is not packaged into the APK. mocks/base32.py is
a port of the C source, and a port is only worth having if it is exact --
a shim that decodes what the firmware rejects would make the app accept BBQr
codes the device refuses, which is a divergence nobody would think to look for.

Python's own base64.b32encode/b32decode is RFC 4648, the same alphabet and the
same bit packing, so it is a usable oracle for the parts that are specified.
The two places it differs from the C code are checked explicitly rather than
assumed:

  * padding. CPython's b32encode always pads; the C encode() takes an
    add_padding flag, and Krux always passes False.
  * case. The C index table holds only A-Z and 2-7, so lowercase raises
    ValueError. CPython's b32decode accepts either unless casefold=False.

    python3 tools/verify_base32.py
"""

import base64
import importlib.util
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_shim():
    path = os.path.join(APP, "mocks", "base32.py")
    spec = importlib.util.spec_from_file_location("mocks.base32", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mocks.base32"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    b32 = load_shim()
    print("  shim: %s" % b32.__file__)
    print("  alphabet: %r" % b32.B32CHARS)
    print()

    failures = 0

    # 1. encode must agree with RFC 4648, unpadded
    print("  encode vs base64.b32encode (unpadded)")
    print("    %-10s %-34s %-34s %s" % ("n bytes", "shim", "rfc4648", "match"))
    print("    " + "-" * 92)
    for n in (0, 1, 2, 3, 4, 5, 7, 8, 9, 16, 31, 32, 33, 64, 100, 255, 256, 1000):
        data = bytes((i * 7 + n) % 256 for i in range(n))
        got = b32.encode(data, False)
        want = base64.b32encode(data).decode("ascii").rstrip("=")
        same = got == want
        failures += 0 if same else 1
        shown_got = got if len(got) <= 32 else got[:16] + "..." + got[-8:]
        shown_want = want if len(want) <= 32 else want[:16] + "..." + want[-8:]
        print("    %-10d %-34s %-34s %s" % (n, shown_got, shown_want,
                                            "ok" if same else "MISMATCH"))
    print()

    # 2. decode must invert encode, and agree with RFC 4648
    print("  decode vs base64.b32decode")
    print("    %-10s %-24s %s" % ("n bytes", "round trip", "vs rfc4648"))
    print("    " + "-" * 60)
    for n in (0, 1, 2, 3, 4, 5, 7, 8, 9, 16, 31, 32, 33, 64, 100, 255, 256, 1000):
        data = bytes((i * 13 + n) % 256 for i in range(n))
        enc = b32.encode(data, False)
        rt = b32.decode(enc)
        rfc = base64.b32decode(enc + "=" * ((8 - len(enc) % 8) % 8))
        rt_ok = rt == data
        rfc_ok = rfc == data
        failures += 0 if (rt_ok and rfc_ok) else 1
        print("    %-10d %-24s %s" % (n, "ok" if rt_ok else "ROUND TRIP FAIL",
                                     "ok" if rfc_ok else "MISMATCH"))
    print()

    # 3. padding, since Krux always passes False. The encoded length is
    #    ceil(n*8/5), so only some input sizes actually need padding -- a
    #    10-byte input encodes to 16 chars, already a multiple of 8. Test the
    #    sizes that do need it, and the one that does not, so a shim that
    #    always padded would be caught too.
    print("  add_padding")
    for n, expect_padding in ((1, True), (3, True), (4, True), (5, False),
                              (10, False), (16, True)):
        data = bytes((i * 3) % 256 for i in range(n))
        unpadded = b32.encode(data, False)
        padded = b32.encode(data, True)
        needs = len(unpadded) % 8 != 0
        if needs != expect_padding:
            print("    (skipped n=%d: encoded to %d chars, padding not applicable)"
                  % (n, len(unpadded)))
            continue
        ok = ("=" not in unpadded
              and len(padded) % 8 == 0
              and padded.startswith(unpadded)
              and b32.decode(padded) == data)
        failures += 0 if ok else 1
        print("    n=%-3d unpadded %2d chars, padded %2d chars (%d '='), decodes: %s  %s"
              % (n, len(unpadded), len(padded),
                 len(padded) - len(unpadded), b32.decode(padded) == data,
                 "ok" if ok else "FAIL"))
    print()

    # 4. strictness: the C table is uppercase-only, so lowercase must raise.
    #    Checked explicitly because this is where a "helpful" port would
    #    silently diverge from the device.
    print("  strictness (must match the C index table: A-Z and 2-7 only)")
    for label, text, should_raise in (
        ("uppercase", b32.encode(b"\x00\x01\x02", False), False),
        ("lowercase", b32.encode(b"\x00\x01\x02", False).lower(), True),
        ("'0' (not in alphabet)", "AAAA0AAA", True),
        ("'1' (not in alphabet)", "AAAA1AAA", True),
        ("'8' (not in alphabet)", "AAAA8AAA", True),
        ("padding tolerated", b32.encode(b"\x00\x01\x02", False) + "======", False),
    ):
        try:
            b32.decode(text)
            raised = False
        except ValueError:
            raised = True
        ok = raised == should_raise
        failures += 0 if ok else 1
        print("    %-24s raises=%-5s expected=%-5s %s"
              % (label, raised, should_raise, "ok" if ok else "DIVERGES"))
    print()

    print("  %d problem(s)" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
