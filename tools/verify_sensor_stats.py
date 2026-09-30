#!/usr/bin/env python3
"""Does the Pillow-based entropy statistics match the numpy original exactly?

``mocks/sensor.py`` feeds ``r_std``/``g_std``/``b_std`` into Krux's entropy
thresholds, which decide whether a camera frame is noisy enough to be a
credible random seed. A mismatch there is a security decision, not a
cosmetic one, so it is worth a check that does not take the word of the
implementation that replaced the original.

This compares the shipped ``MockStatistics`` against the numpy version it
replaced, over degenerate frames as well as realistic ones. Flat frames,
two-value frames and near-flat frames are exactly where a standard deviation
is easiest to get wrong: all of them sit where a naive two-pass variance and a
one-pass variance disagree most in the last bits, which is where the ``//``
truncation decides the answer.

Needs ``numpy`` and ``pillow``; neither is in the Android build, so this runs
on the host, not on device.

    python3 tools/verify_sensor_stats.py
"""

import os
import random
import sys
import types

import numpy as np  # pylint: disable=import-error
from PIL import Image  # pylint: disable=import-error

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stub(name, **attrs):
    """Install a stand-in module so the real sensor.py can be imported."""
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


# sensor.py pulls in Kivy and the camera QR reader, none of which matter to the
# statistics. Stub them so this exercises the shipped file rather than a copy.
_stub("kivy")
_stub("kivy.uix")
_stub("kivy.uix.widget", Widget=type("Widget", (), {}))
_stub("kivy.properties", ObjectProperty=lambda *a, **k: None)
sys.path.insert(0, APP)
_stub("mocks.qrreader", QRReader=type("QRReader", (), {}))
_stub("mocks", __path__=[os.path.join(APP, "mocks")])

# pylint: disable=wrong-import-position
from mocks.sensor import MockStatistics  # noqa: E402


def numpy_original(img, height, width):
    """The implementation that was in mocks/sensor.py before the change."""
    # The original returned early for a falsy buffer, before touching numpy.
    if not img:
        return (0, 0, 0)
    try:
        arr = np.frombuffer(img, dtype=np.uint8).reshape((height, width, 4))
    except Exception:  # pylint: disable=broad-except
        return (10, 10, 10)
    # Cast back to int: the original shipped numpy scalars, but what it fed the
    # entropy thresholds was the integer, and an exact scalar equality test
    # would compare a float against an int and hide the point of the check.
    return tuple(
        int((np.std(arr[:, :, chan]) * 100) // 255) for chan in range(3)
    )


def _fmt(triple):
    return "(%d, %d, %d)" % triple


def pillow_new(img, height, width):
    """The shipped implementation, which uses Pillow histograms."""
    stats = MockStatistics(img, height, width)
    return tuple(int(v) for v in (stats.r_std, stats.g_std, stats.b_std))


W, H = 320, 240
P = W * H


def solid(value):
    return bytes([value]) * (P * 4)


def two_value(a, b):
    return bytes(([a, b] * (P * 2)))[: P * 4]


def near_flat():
    """A frame with a whisper of variation -- the cancellation case."""
    rnd = random.Random(7)
    return bytes(rnd.choice((100, 101, 102)) for _ in range(P * 4))


def gradient_flat():
    buf = bytearray()
    for i in range(P):
        buf += bytes((i % 256, (i // 3) % 256, (i // 7) % 256, 255))
    return bytes(buf)


def random_noise(seed):
    rnd = random.Random(seed)
    return bytes(rnd.randrange(256) for _ in range(P * 4))


def single_hot_pixel():
    """One pixel differs in an otherwise flat frame."""
    buf = bytearray([128]) * (P * 4)
    buf[0] = 255
    return bytes(buf)


def realistic_photo_like(seed):
    """Smooth-ish content, closer to what a camera actually produces."""
    rnd = random.Random(seed)
    buf = bytearray()
    r = g = b = 100
    for _ in range(P):
        r = max(0, min(255, r + rnd.randrange(-6, 7)))
        g = max(0, min(255, g + rnd.randrange(-6, 7)))
        b = max(0, min(255, b + rnd.randrange(-6, 7)))
        buf += bytes((r, g, b, 255))
    return bytes(buf)


CASES = [
    ("all zeros", solid(0)),
    ("all 255", solid(255)),
    ("all 128 (flat mid)", solid(128)),
    ("two value 0/255", two_value(0, 255)),
    ("two value 100/101 (near flat)", two_value(100, 101)),
    ("near flat 100..102", near_flat()),
    ("gradient", gradient_flat()),
    ("random noise seed 1", random_noise(1)),
    ("random noise seed 2", random_noise(2)),
    ("single hot pixel", single_hot_pixel()),
    ("photo-like seed 3", realistic_photo_like(3)),
    ("photo-like seed 4", realistic_photo_like(4)),
    # the two guards, which are where a stub most easily drifts
    ("wrong buffer size", bytes(P * 4 - 1)),
    ("empty buffer", b""),
]


def main():
    print("  app under test: %s" % APP)
    print("  %-32s %-14s %-14s %s" % ("case", "numpy", "pillow", "match"))
    print("  " + "-" * 66)

    failures = 0
    for name, buf in CASES:
        expected = numpy_original(buf, H, W)
        actual = pillow_new(buf, H, W)
        ok = expected == actual
        failures += 0 if ok else 1
        print("  %-32s %-14s %-14s %s"
              % (name, _fmt(expected), _fmt(actual), "ok" if ok else "MISMATCH"))

    print()
    print("  %d cases, %d mismatch(es)" % (len(CASES), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
