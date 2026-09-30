#!/usr/bin/env python3
"""Can the app parse every QR format a PSBT can arrive in?

The camera is only half the problem. Once a frame is decoded, src/krux/qr.py's
QRPartParser has to recognise the format and accumulate it, and that code
imports from `uUR` -- the module the app replaces with a pure-Python shim
(mocks/uUR.py) because the firmware's C extension does not exist on Android.

That makes the shim part of the QR *protocol* surface, not just a platform
detail, and nothing in the tree checks the two agree. When the upstream Krux
that this app vendors started calling `from uUR import DECODER_OK`, every BC-UR
scan raised ImportError inside the capture loop -- which escapes before
ctx.camera.stop_sensor(), so the camera preview was never torn down.

This exercises QRPartParser directly, with no camera and no device, across the
formats a PSBT realistically arrives in:

    BC-UR, single part        the common case
    BC-UR, multi part         the animated/"less dense" display mode
    BBQR,  single part
    BBQR,  multi part
    plain base64 PSBT         FORMAT_NONE, the non-fountain fallback

    python3 tools/verify_qr_formats.py
    python3 tools/verify_qr_formats.py --shim /path/to/an/older/uUR.py

Needs Krux's venv for the app's ur/urtypes and the qrcode module.
"""

import argparse
import importlib.util
import json
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def die(msg):
    sys.exit("error: " + msg)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def install_shim(path):
    """Load a uUR shim and make it the module `uUR` resolves to.

    mocks/uUR.py ends with `if "uUR" not in sys.modules: sys.modules["uUR"] = ...`,
    so it only registers itself when nothing is there yet. Krux's venv has the
    real C extension installed, which would otherwise win and quietly become the
    thing under test -- the first version of this script had exactly that bug
    and reported a pass that proved nothing.
    """
    sys.path.insert(0, os.path.join(APP, "src"))
    sys.modules.setdefault("ujson", json)
    mod = load_module("mocks.uUR", path)
    sys.modules["uUR"] = mod
    return mod


def install_base32():
    """Register the MicroPython-compat shims that src/krux imports by name.

    mocks/ is the app's MicroPython compatibility layer: board, sensor, lcd,
    ujson, uUR, deflate, baseconv and the rest are MicroPython or MaixPy
    modules that do not exist on CPython, and each one re-registers itself
    under its bare name via a `sys.modules[...] = ...` tail. src/krux then
    imports them as if they were builtins.

    base32 was the one member of that set that was missing, which is why every
    BBQr scan raised ModuleNotFoundError. Install the whole set here rather
    than just base32, so that a future omission shows up as a failure in this
    test instead of on a phone.
    """
    names = ("base32", "deflate")
    installed = {}
    for name in names:
        path = os.path.join(APP, "mocks", name + ".py")
        if not os.path.exists(path):
            die("mocks/%s.py is missing -- %s cannot work without it" % (name, name))
        installed[name] = load_module("mocks." + name, path)
        sys.modules[name] = installed[name]
    return installed


def make_ur_frames(shim, payload, urtype="crypto-psbt", max_fragment_len=20,
                   min_fragment_len=10):
    """Encode a payload as a real BC-UR and return its frames.

    The cbor goes in as bytes, which is what the app's own callers pass
    (Types.psbt_to_cbor(...).data in src/krux/psbt.py). min_fragment_len has to
    come down for a small payload: the encoder asserts rather than raising when
    it cannot find a fragment length, so the default of 10 with an 8-byte
    message is a bare AssertionError that says nothing about what went wrong.
    """
    from ur.ur import UR  # noqa: E402
    encoder = shim.UREncoder(UR(urtype, payload), max_fragment_len,
                             min_fragment_len=min_fragment_len)
    frames, single = [], encoder.is_single_part()
    for _ in range(300):
        frames.append(encoder.next_part())
        if not single and encoder.is_complete():
            break
    return frames


def make_bbqr_frames(payload, encoding="2", file_type="P", part_size=40):
    """Hand-build BBQr parts: B$<enc><type><total><index><payload>, base36.

    encode_bbqr picks the encoding itself for "Z" (falling back to "2" when
    deflate does not help), so the requested encoding is taken from what it
    actually chose -- the header has to agree with the body or the parser's
    own consistency check rejects it.
    """
    from krux.bbqr import encode_bbqr  # noqa: E402

    code = encode_bbqr(payload, encoding=encoding, file_type=file_type)
    body = code.payload
    chunks = [body[i:i + part_size] for i in range(0, len(body), part_size)] or [""]
    total = len(chunks)
    return [
        "B$%s%s%s%s%s" % (code.encoding, code.file_type,
                          _b36(total, 2), _b36(i, 2), chunk)
        for i, chunk in enumerate(chunks)
    ]


def make_pmofn_frames(payload, parts=4):
    """pMofN is Krux's own framing: `p<N>of<M> <chunk>`, alphanumeric."""
    import base64  # noqa: E402

    body = base64.b32encode(payload).decode("ascii").rstrip("=")
    size = (len(body) + parts - 1) // parts
    chunks = [body[i:i + size] for i in range(0, len(body), size)]
    return ["p%dof%d %s" % (i + 1, len(chunks), chunk)
            for i, chunk in enumerate(chunks)]


def _b36(value, width):
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = digits[rem] + out
    return out.rjust(width, "0")


def drive(parser_cls, frames, label, results):
    """Feed frames through a fresh parser; record whether it completes."""
    parser = parser_cls()
    last_error = None
    for frame in frames:
        try:
            parser.parse(frame)
        except Exception as e:  # pylint: disable=broad-except
            last_error = "%s: %s" % (type(e).__name__, e)
            break
        if parser.is_complete():
            break
    ok = parser.is_complete() and last_error is None
    results.append((label, ok, parser.format, last_error))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shim", default=os.path.join(APP, "mocks", "uUR.py"),
                    help="uUR shim to test (default: the app's own)")
    ap.add_argument("--krux", default=os.path.join(APP, "..", "krux"),
                    help="Krux checkout, for its venv-interpreter path only")
    args = ap.parse_args()

    shim_path = os.path.abspath(args.shim)
    if not os.path.exists(shim_path):
        die("no shim at %s" % shim_path)

    shim = install_shim(shim_path)
    b32mod = install_base32()
    sys.path.insert(0, os.path.join(APP, "src"))
    from krux.qr import QRPartParser  # noqa: E402

    print("  shim      : %s" % shim_path)
    print("  uUR       : %s" % sys.modules["uUR"].__file__)
    print("  base32    : %s" % b32mod["base32"].__file__)
    print()

    # Two payloads: one small enough that every encoding emits a single part,
    # one large enough to force a multi-part stream. A "single part" case has
    # to be built from the small one -- truncating a multi-part stream to its
    # first frame can never complete, so testing that proves nothing.
    #
    # The large payload is 500 bytes because src/ur/fountain_encoder.py has a
    # latent bug: partition_message() pads the final fragment with
    # `fragment.append(0)`, which raises TypeError on the bytes slice that
    # utils.split() returns. So encoding only works when the payload divides
    # evenly by the fragment length. 500 = 25 x 20, and 20 is the
    # max_fragment_len below. See the note in tools/verify_qr_formats.md.
    small = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    payload = bytes(i % 256 for i in range(500))
    results = []

    # --- BC-UR ------------------------------------------------------------
    small_ur = make_ur_frames(shim, small, max_fragment_len=200, min_fragment_len=2)
    drive(QRPartParser, small_ur, "UR   single part", results)
    ur_frames = make_ur_frames(shim, payload)
    drive(QRPartParser, ur_frames, "UR   multi part (%d frames)" % len(ur_frames),
          results)
    # The decoder is fed every frame the encoder emits, including the redundant
    # tail after completion. A shim that reported those as a hard error would
    # fail here, because qr.py raises ValueError on a non-OK receive_part.
    drive(QRPartParser, ur_frames + ur_frames,
          "UR   frames replayed (redundant tail)", results)

    # --- BBQr, every encoding x file type ---------------------------------
    # KNOWN_ENCODINGS = H, 2, Z ; KNOWN_FILETYPES = P, T, J, U
    for enc in ("H", "2", "Z"):
        for ftype in ("P", "T", "J", "U"):
            one = make_bbqr_frames(small, encoding=enc, file_type=ftype,
                                   part_size=4096)
            drive(QRPartParser, one, "BBQr %s%s single part" % (enc, ftype), results)
            frames = make_bbqr_frames(payload, encoding=enc, file_type=ftype)
            drive(QRPartParser, frames,
                  "BBQr %s%s multi part (%d)" % (enc, ftype, len(frames)), results)

    # --- pMofN and plain --------------------------------------------------
    one = make_pmofn_frames(small, parts=1)
    drive(QRPartParser, one, "pMofN single part", results)
    pmofn = make_pmofn_frames(payload, parts=4)
    drive(QRPartParser, pmofn, "pMOfN multi part (%d frames)" % len(pmofn), results)
    drive(QRPartParser, ["cHNidP8BAH0" + "A" * 300],
          "plain base64 (FORMAT_NONE)", results)

    names = {0: "NONE", 1: "pMofN", 2: "UR", 3: "BBQR"}
    print("  %-38s %-7s %-7s %s" % ("case", "parsed", "format", "error"))
    print("  " + "-" * 92)
    failures = 0
    for label, ok, fmt, err in results:
        failures += 0 if ok else 1
        print("  %-38s %-7s %-7s %s"
              % (label, "yes" if ok else "NO", names.get(fmt, str(fmt)),
                 err or ""))

    print()
    print("  %d failure(s)" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
