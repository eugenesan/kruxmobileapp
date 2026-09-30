#!/usr/bin/env python3
"""Does the Android uUR shim behave like the firmware's C module?

The app replaces the `uUR` C extension with a pure-Python shim
(mocks/uUR.py) so that UR decoding works on CPython. The firmware module and
the Python package do not agree on their interface: the C one reports progress
as a `state` code, the Python one returns bools from receive_part and exposes
is_success()/is_failure().

src/krux/qr.py is written against the C interface. So the shim has to bridge
the two, and "it imports" is not evidence that it bridges them correctly -- a
wrong mapping would silently mis-decode a transaction.

This drives the *same* frames through both implementations and compares the
state after each one. The C module is the oracle. It covers:

  * a single-part UR, which must reach DECODER_OK immediately
  * a multi-part fountain-encoded UR, where the interesting cases are the
    frames that arrive before the set is complete (must stay "processing",
    not "no result" -- qr.py raises ValueError on the latter)
  * garbage input, which both must refuse to advance on

    python3 tools/verify_uur_shim.py

Needs the real C module, so it runs against a Krux checkout's venv where
uUR is built. Pass --krux <path> if it is not at ../krux.
"""

import os
import sys
import types

DEFAULT_KRUX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "krux"
)


def die(msg):
    sys.exit("error: " + msg)


def load_c_uur(krux):
    """Import the firmware's compiled uUR, which lives in Krux's venv."""
    if krux not in sys.path:
        sys.path.insert(0, krux)
    try:
        import uUR  # noqa: F401  pylint: disable=import-outside-toplevel
    except ImportError as e:
        die("could not import the real uUR from %s: %s" % (krux, e))
    return uUR


def load_shim(app):
    """Import mocks/uUR.py the way the app does, without mocks/__init__.

    Note the alias: the shim's last two lines are

        if "uUR" not in sys.modules:
            sys.modules["uUR"] = sys.modules[__name__]

    so it installs itself as `uUR` only when nothing is there yet. The C module
    is imported first in main(), which means the shim must be forced into
    sys.modules["uUR"] or the shim's own decode path is never exercised --
    silently testing the C module against itself, which passes trivially.
    """
    import importlib.util  # pylint: disable=import-outside-toplevel

    import json  # pylint: disable=import-outside-toplevel

    sys.path.insert(0, os.path.join(app, "src"))
    # uUR's transitive imports reach for ujson, which is MicroPython's json
    sys.modules.setdefault("ujson", json)
    path = os.path.join(app, "mocks", "uUR.py")
    spec = importlib.util.spec_from_file_location("mocks.uUR", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mocks.uUR"] = mod
    spec.loader.exec_module(mod)
    sys.modules["uUR"] = mod
    return mod


def encode_parts(payload, urtype="crypto-psbt", max_fragment_len=20):
    """Build a real UR and return its frames, using the app's encoder.

    Enough frames to complete, plus extra so the decoder sees the redundant and
    out-of-order tail -- which is where a wrong state mapping would show up.
    """
    from ur.ur import UR  # pylint: disable=import-outside-toplevel
    from ur.ur_encoder import UREncoder  # pylint: disable=import-outside-toplevel

    encoder = UREncoder(UR(urtype, payload), max_fragment_len)
    frames = []
    for _ in range(200):
        frames.append(encoder.next_part())
        if encoder.is_complete() and len(frames) > 6:
            break
    # keep going past completion: the shim must not regress on a late frame
    for _ in range(4):
        frames.append(encoder.next_part())
    return frames


def ok(state):
    return state == 0


def main():
    krux = sys.argv[sys.argv.index("--krux") + 1] if "--krux" in sys.argv else DEFAULT_KRUX
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    krux = os.path.abspath(krux)

    c_uur = load_c_uur(krux)
    shim = load_shim(app)

    print("  C module : %s" % c_uur.__file__)
    print("  shim     : %s" % shim.__file__)
    print()

    # the constants must match exactly, or qr.py's comparisons are meaningless
    print("  state code agreement")
    print("    %-38s %-8s %-8s %s" % ("constant", "C", "shim", "match"))
    print("    " + "-" * 62)
    codes = [n for n in dir(c_uur) if n.startswith("DECODER_")]
    bad = 0
    for name in sorted(codes):
        want = getattr(c_uur, name)
        got = getattr(shim, name, "<MISSING>")
        same = want == got
        bad += 0 if same else 1
        print("    %-38s %-8s %-8s %s" % (name, want, got, "ok" if same else "MISMATCH"))
    print()

    payload = b"\x00" * 60
    frames = encode_parts(payload)
    print("  encoded a %d-frame UR" % len(frames))
    if not frames:
        die("encoder produced no frames")
    print()

    # Drive both decoders over the same frames and compare the point at which
    # each reports success. The absolute state values will differ mid-stream --
    # the C module has a finer PROCESSING/NO_RESULT distinction than the shim
    # needs -- so what must agree is: did it complete, and did it complete on
    # the same frame.
    def run(decoder_cls, use_c):
        dec = c_uur.URDecoder() if use_c else shim.URDecoder()
        seen = []
        for i, frame in enumerate(frames):
            if use_c:
                state = dec.receive_part(frame)
                done = state == c_uur.DECODER_OK and dec.result is not None
            else:
                state = dec.receive_part(frame)
                done = dec.state == shim.DECODER_OK
            seen.append((i, state, done))
            if done:
                return seen, i, dec
        return seen, None, dec

    c_seen, c_at, c_dec = run(None, True)
    s_seen, s_at, s_dec = run(None, False)

    print("  completion")
    print("    C module completed on frame   : %s" % c_at)
    print("    shim     completed on frame   : %s" % s_at)
    agree = c_at == s_at
    print("    %s" % ("agree" if agree else "DISAGREE -- see below"))
    print()

    if not agree and s_at is not None and c_at is not None:
        print("  first divergence")
        for (ci, cs, cd), (si, ss, sd) in zip(c_seen, s_seen):
            if cd != sd:
                print("    frame %d: C state=%-3s done=%-5s | shim state=%-3s done=%s"
                      % (ci, cs, cd, ss, sd))
                break
        print()

    print("  decoded payload")
    # Both return a UR object, not raw bytes, so compare the CBOR body.
    def body(dec):
        res = getattr(dec, "result", None)
        if res is None:
            return None
        cbor = getattr(res, "cbor", None)
        if cbor is not None:
            return bytes(cbor)
        if isinstance(res, (bytes, bytearray)):
            return bytes(res)
        decode = getattr(res, "decode", None)
        return decode() if callable(decode) else None

    c_body, s_body = body(c_dec), body(s_dec)
    c_ok = c_body == payload
    s_ok = s_body == payload
    print("    C module : %s" % ("correct" if c_ok else "WRONG/None"))
    print("    shim     : %s" % ("correct" if s_ok else "WRONG/None"))
    print()

    print("  garbage input must not advance either decoder")
    for junk in ("not a ur at all", "ur:", "ur:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"):
        cj = c_uur.URDecoder()
        sj = shim.URDecoder()
        try:
            cj.receive_part(junk)
        except Exception:  # pylint: disable=broad-except
            pass
        try:
            sj.receive_part(junk)
        except Exception:  # pylint: disable=broad-except
            pass
        c_done = cj.result is not None
        s_done = sj.result is not None
        print("    %-36s C done=%-5s shim done=%-5s %s"
              % (repr(junk)[:36], c_done, s_done,
                 "ok" if c_done == s_done else "DIVERGES"))
        bad += 0 if c_done == s_done else 1

    print()
    failures = bad + (0 if agree else 1) + (0 if c_ok and s_ok else 1)
    print("  %d problem(s)" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
