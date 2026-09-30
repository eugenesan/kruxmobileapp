"""CPython port of MicroPython's `base32` module.

src/krux/bbqr.py and src/krux/baseconv.py do `import base32`, which is a
MicroPython built-in module. It is not part of CPython and it is not packaged
into the APK: the bundle on the test device has 65 modules and none of them is
base32. So every BBQr scan died with ModuleNotFoundError inside the capture
loop, which is a crash rather than a failed scan. See the note at the end of
mocks/uUR.py about how a raise in that loop affects the camera teardown.

This is a faithful port of

    firmware/MaixPy/components/micropython/port/src/base32/base32.c

so that behaviour is identical to the firmware rather than merely similar. In
particular it is *strict*: the C index table holds only A-Z and 2-7, so a
lowercase character is invalid and raises, exactly as on the device. Loosening
that here would make the app accept BBQr codes that Krux hardware rejects, which
is the kind of one-platform divergence that is much harder to debug later than
a clean failure now.

Like mocks/uUR.py, this registers itself as `base32` so that the import inside
krux resolves without `mocks/` being on sys.path. Adding `mocks/` to the path
instead would be wrong: it contains image.py, qrcode.py and machine.py, and
would shadow the real `qrcode` distribution that krux/qr.py imports.
"""

import sys

B32CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
BASE32_MASK = 0x1F

# Built once, the way the C code caches it in init_base32_index(). -1 marks a
# character that is not in the alphabet.
_INDEX = [-1] * 256
for _i, _c in enumerate(B32CHARS):
    _INDEX[ord(_c)] = _i


def decode(encoded_str):
    """Decode a base32 string, ignoring any trailing '=' padding.

    Mirrors base32_decode(): strip padding, then accumulate five bits per
    character and emit a byte every time eight are available.
    """
    if isinstance(encoded_str, (bytes, bytearray)):
        encoded_str = bytes(encoded_str).decode("ascii")
    encoded_str = encoded_str.rstrip("=")

    out = bytearray()
    buffer = 0
    bits_left = 0
    for char in encoded_str:
        index = _INDEX[ord(char) & 0xFF] if ord(char) < 256 else -1
        if index == -1:
            raise ValueError("Invalid Base32 character")
        buffer = (buffer << 5) | index
        bits_left += 5
        while bits_left >= 8:
            bits_left -= 8
            out.append((buffer >> bits_left) & 0xFF)
            buffer &= (1 << bits_left) - 1
    return bytes(out)


def encode(data, add_padding=False):
    """Encode bytes as an uppercase base32 string.

    Mirrors base32_encode(). `add_padding` pads the output with '=' out to a
    multiple of eight characters; Krux always passes False, since the base32 in
    a BBQr part is a fixed-width field rather than a standalone encoding.
    """
    if isinstance(data, str):
        data = data.encode("utf8")
    data = bytes(data)

    out = bytearray()
    buffer = 0
    bits_left = 0
    for byte in data:
        buffer = (buffer << 8) | byte
        bits_left += 8
        while bits_left >= 5:
            bits_left -= 5
            out.append(ord(B32CHARS[(buffer >> bits_left) & BASE32_MASK]))
            buffer &= (1 << bits_left) - 1
    if bits_left > 0:
        buffer <<= 5 - bits_left
        out.append(ord(B32CHARS[buffer & BASE32_MASK]))
    if add_padding:
        out.extend(b"=" * ((8 - (len(out) % 8)) % 8))
    return out.decode("ascii")


if "base32" not in sys.modules:
    sys.modules["base32"] = sys.modules[__name__]
