# The MIT License (MIT)

# Copyright (c) 2021-2025 Krux contributors

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""CPython shim that mirrors the firmware's uUR C module by re-exporting
the pure-Python `urtypes` and `foundation-ur-py` packages under the same
public surface (UR, URDecoder, UREncoder, Types).
"""

import sys

from ur.ur import UR
from ur.ur_decoder import URDecoder as _URDecoder
from ur.ur_encoder import UREncoder as _UREncoder

from urtypes.bytes import Bytes as _Bytes
from urtypes.crypto.account import Account as _Account
from urtypes.crypto.bip39 import BIP39 as _BIP39
from urtypes.crypto.output import Output as _Output
from urtypes.crypto.psbt import PSBT as _PSBT


class UREncoder(_UREncoder):
    """uUR's encoder emits uppercase Bytewords; the Python encoder emits
    lowercase. Match firmware behaviour by uppercasing here."""

    def next_part(self):
        return super().next_part().upper()


# The firmware's uUR C module reports progress as a state code, and src/krux/qr.py
# compares against these names. Taken from the module itself rather than
# invented:
#
#   krux/.venv/bin/python -c "import uUR; print({n: getattr(uUR, n) for n in dir(uUR) if n.startswith('DECODER_')})"
#
# The pure-Python decoder this shim wraps has no equivalent, so without these
# the `from uUR import DECODER_OK` in qr.py raises ImportError the first time a
# BC-UR frame is parsed. That is a crash inside the capture loop, which is worse
# than a failed scan: the exception escapes before ctx.camera.stop_sensor() runs,
# so the camera widget is never detached and the preview stays on screen with no
# way out. Plain and pMofN QRs never reach that import, which is why mnemonic
# scanning works and UR does not.
DECODER_OK = 0
DECODER_PROCESSING = 1
DECODER_NO_RESULT = 2
DECODER_ERR_INVALID_SCHEME = 16
DECODER_ERR_INVALID_TYPE = 17
DECODER_ERR_INVALID_PATH_LENGTH = 18
DECODER_ERR_INVALID_SEQUENCE_COMPONENT = 19
DECODER_ERR_INVALID_FRAGMENT = 20
DECODER_ERR_INVALID_PART = 21
DECODER_ERR_INVALID_CHECKSUM = 22
DECODER_ERR_MEMORY = 23
DECODER_ERR_NULL_POINTER = 24


class URDecoder(_URDecoder):
    """uUR's decoder exposes the state codes above plus expected_part_count,
    processed_parts_count and result as plain attributes. The pure-Python
    decoder instead reports through is_success()/is_failure()/is_complete() and
    returns a bool from receive_part, so the two are bridged here.

    Without this, src/krux/qr.py cannot run its UR path at all on Android: it
    reads DECODER_OK/DECODER_NO_RESULT/DECODER_ERR_INVALID_CHECKSUM and
    `decoder.state`, none of which the Python decoder provides. Real hardware
    never notices, because the C module has them; only this shim does not."""

    @property
    def expected_part_count(self):
        if self.fountain_decoder.expected_part_indexes is None:
            return 0
        return len(self.fountain_decoder.expected_part_indexes)

    @property
    def processed_parts_count(self):
        return self.fountain_decoder.processed_parts_count

    @property
    def state(self):
        """The current state code, as the C module would report it.

        Read-only on purpose: qr.py only ever tests it for equality against
        DECODER_OK, and a writable alias onto is_complete() would let a caller
        drive the decoder by assigning to what looks like plain state.
        """
        if self.result is not None:
            return DECODER_OK
        if self.is_failure():
            return DECODER_NO_RESULT
        return DECODER_PROCESSING

    def receive_part(self, data):
        """Feed one UR frame; return a state code, as the C module does.

        The Python decoder returns a bool and swallows its own errors, so the
        two success-ish outcomes are mapped here: a frame that produced a
        result is DECODER_OK, and anything still in progress is
        DECODER_PROCESSING.

        A frame that did not advance the fountain decoder is reported as
        DECODER_PROCESSING rather than DECODER_NO_RESULT. qr.py raises
        ValueError on NO_RESULT, and a fountain-encoded multi-part UR
        legitimately receives redundant and out-of-order frames before it can
        complete; calling those a hard failure would abort scans that the
        firmware decoder would have finished. tools/verify_uur_shim.py checks
        this mapping against the real C module.
        """
        if self.result is not None:
            return DECODER_OK
        super().receive_part(data)
        if self.result is not None:
            return DECODER_OK
        return DECODER_PROCESSING



class Types:
    CRYPTO_PSBT_TYPE = "crypto-psbt"
    CRYPTO_BIP39_TYPE = "crypto-bip39"
    CRYPTO_OUTPUT_TYPE = "crypto-output"
    CRYPTO_ACCOUNT_TYPE = "crypto-account"

    @staticmethod
    def psbt_from_cbor(cbor):
        return _PSBT.from_cbor(cbor).data

    @staticmethod
    def psbt_to_cbor(data):
        return _PSBT(data).to_cbor()

    @staticmethod
    def bytes_from_cbor(cbor):
        return _Bytes.from_cbor(cbor).data

    @staticmethod
    def bytes_to_cbor(data):
        return _Bytes(data).to_cbor()

    @staticmethod
    def bip39_words_from_cbor(cbor):
        return _BIP39.from_cbor(cbor).words

    @staticmethod
    def output_from_cbor(cbor):
        return _Output.from_cbor(cbor).descriptor()

    @staticmethod
    def output_from_cbor_account(cbor):
        return _Account.from_cbor(cbor).output_descriptors[0].descriptor()


if "uUR" not in sys.modules:
    sys.modules["uUR"] = sys.modules[__name__]
