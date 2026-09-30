# The MIT License (MIT)

# Copyright (c) 2021-2026 Krux contributors

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
"""Choosing the signature hash for a PSBT, and checking what came out.

A PSBT input may declare a hash type, and it decides which algorithm signs and
what the signature commits to, so the device picks from an allowlist, shows the
user which one it picked, and verifies the result.

The unified opt-in is a 0x20 bit in the hash type byte (Bitcoin Knots,
doc/unified-sighash.md). One message format for every script type, committing
to every spent amount, which closes CVE-2020-14199 for those inputs.
"""

from embit.psbt import sighash_types_agree
from embit.transaction import SIGHASH
from .krux_settings import t

# an input this wallet holds would be skipped, leaving the transaction signed
# in part
REFUSED_PARTIAL = "partial"
# every input would be signed, but with types no one label covers
REFUSED_MIXED = "mixed"

# Slot id for a signature under a key that has no valid curve point. embit would
# never have signed with such a key, so it is by definition not one of ours.
# Distinct from any serialized key, so it can never collide with a real one.
NOT_OURS = b"\x00not-ours"

# The types we will ask sign_with for; anything else falls back to DEFAULT,
# under which embit signs the inputs asking for ALL and skips the rest.
#
# An allowlist because the value comes off the wire from a host we do not trust.
# NONE commits to no outputs, SINGLE with no matching output signs a constant
# reusable against any transaction spending that key, ANYONECANPAY leaves the
# other inputs uncommitted. None of them are shown to a signer, so a
# transaction asking for one would review as an ordinary send.
#
# This bounds what we ask for, not what comes back: under DEFAULT, sign_with
# still honours an input's own opt-in bit, so a PSBT declaring 0x21 is signed
# as 0x21 regardless. That commits to everything, which is the point.
SIGNABLE_SIGHASH_TYPES = frozenset(
    {
        None,  # the PSBT does not say
        SIGHASH.DEFAULT,  # taproot
        SIGHASH.ALL,
        SIGHASH.UNIFIED | SIGHASH.ALL,
    }
)


class PSBTRefusedError(Exception):
    """No single hash type describes this PSBT. See REFUSED_PARTIAL/MIXED."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class PSBTSignError(Exception):
    """Signing failed on a PSBT the user had already approved."""


def derivation_is_ours(key, public_key, derivation_path_obj):
    """Whether a derivation on an input or output belongs to this wallet's key.

    A coordinator given only an xpub sends a zero fingerprint, so that case
    falls back to deriving the key and comparing, as _fill_zero_fingerprint_scope
    does.
    """
    if derivation_path_obj.fingerprint == key.fingerprint:
        return True

    if derivation_path_obj.fingerprint == b"\x00\x00\x00\x00":
        try:
            derived = key.root.derive(derivation_path_obj.derivation)
            return derived.key.sec() == public_key.sec()
        except Exception:  # pylint: disable=W0703
            return False
    return False


def input_is_ours(key, inp):
    """Whether any derivation on this input belongs to this wallet's key"""
    derivations = list(inp.bip32_derivations.items()) + [
        (pub, derivation)
        for pub, (_leaves, derivation) in inp.taproot_bip32_derivations.items()
    ]
    return any(
        derivation_is_ours(key, pub, derivation) for pub, derivation in derivations
    )


def effective_sighash_type(inp, requested):
    """The hash type this input will actually be signed with.

    Mirrors what sign_with resolves: an input declaring nothing takes the
    requested type, and DEFAULT means ALL off taproot, where the byte has to
    name an output type. The screen shows this rather than the requested type,
    since a screen naming a type no signature carries is worse than silence.
    """
    declared = inp.sighash_type
    effective = requested if declared is None else declared
    if not inp.is_taproot and effective == SIGHASH.DEFAULT:
        effective = SIGHASH.ALL
    # unreachable today; kept so this stays a mirror of sign_with
    if (
        requested is not None
        and requested != SIGHASH.DEFAULT
        and (effective | requested) & SIGHASH.UNIFIED
    ):
        effective = requested
    return effective


def sighash_type(psbt):
    """The hash type to pass to sign_with: the inputs' own where they all agree
    on a signable one, DEFAULT otherwise."""
    declared = {inp.sighash_type for inp in psbt.inputs}
    if len(declared) != 1 or not declared <= SIGNABLE_SIGHASH_TYPES:
        return SIGHASH.DEFAULT
    return declared.pop() or SIGHASH.DEFAULT


def unsignable_inputs(psbt, key):
    """Indices of this wallet's inputs that the signer will skip.

    Scoped to our own inputs: one we hold no key for gets no signature whatever
    its type, and a co-signer finishes it, so counting those would refuse the
    collaborative transactions this device exists to join.
    """
    requested = sighash_type(psbt)
    return [
        i
        for i, inp in enumerate(psbt.inputs)
        if inp.sighash_type is not None
        and not sighash_types_agree(inp.sighash_type, requested)
        and input_is_ours(key, inp)
    ]


def screen_sighash_type(psbt, key):
    """(hash_type, None) when there is one honest thing to show, else
    (None, reason)."""
    requested = sighash_type(psbt)

    if unsignable_inputs(psbt, key):
        return None, REFUSED_PARTIAL

    ours = [inp for inp in psbt.inputs if input_is_ours(key, inp)]
    effective = {
        effective_sighash_type(inp, requested) for inp in (ours or psbt.inputs)
    }
    if len(effective) != 1:
        return None, REFUSED_MIXED

    # the declared type is four unbounded little endian bytes off the wire, so
    # the fallback above can carry something we would never sign
    shown = effective.pop()
    if shown not in SIGNABLE_SIGHASH_TYPES:
        return None, REFUSED_MIXED
    return shown, None


def sighash_label(hash_type):
    """The review screen's line naming the message about to be signed.

    Labelled in both states: a host that rewrites a request for the unified
    message down to the standard one gets a standard signature, and labelling
    only the opt-in would make that indistinguishable from never saying
    anything.

    Kept to one display line, since the summary is at the screen height on the
    narrowest device we run on and a wrap costs the fee below it.
    """
    if hash_type & SIGHASH.UNIFIED:
        label = t("Unified")
    else:
        label = t("Standard")
    return "%s 0x%02x" % (label, hash_type)


def signed_hash_types(tx):
    """Every signature on this PSBT: where it sits, its hash type, its bytes.

    Read off the signatures rather than predicted. The bytes come back too
    because a host can pre-populate any slot, and a caller comparing only which
    slots are occupied would read a planted signature as one of ours.

    A key that cannot be serialized is recorded under NOT_OURS rather than
    allowed to raise. Raising let a host abort a signing attempt outright, by
    planting a malformed key in any slot, which is the opposite of what this
    function is for.
    """

    def hash_type(raw):
        # a taproot key path signature is 64 bytes with no trailing byte, which
        # is DEFAULT rather than an absent hash type
        if not raw:
            return None
        return raw[-1] if len(raw) != 64 else SIGHASH.DEFAULT

    def slot(pubkey):
        """A stable id for the slot this signature sits in.

        Normally the key's serialized bytes. embit builds a PublicKey from raw
        bytes without checking them and only fails when the point is serialized,
        so a host can plant a key that has none; that one gets its own id
        instead. It has to stay visible here: skipping it would hide the
        signature from the before/after comparison in sign_with, and a planted
        signature that goes unnoticed is exactly what that comparison exists to
        catch. Since embit could not have signed with such a key, it is not one
        of ours, and the comparison rejects it on that basis.
        """
        try:
            return bytes(pubkey.sec())
        except ValueError:
            return NOT_OURS

    found = {}
    for i, inp in enumerate(tx.inputs):
        for pubkey, sig in inp.partial_sigs.items():
            raw = bytes(sig)
            found[(i, "partial", slot(pubkey))] = (hash_type(raw), raw)
        # taproot_sigs is keyed by (pubkey, leaf_hash), not by pubkey, so the
        # key is the tuple and the pubkey is its first element. Using the whole
        # tuple as the slot would pass it to slot() and fail on .sec().
        for (pubkey, _leaf), sig in inp.taproot_sigs.items():
            raw = bytes(sig)
            found[(i, "taproot", slot(pubkey))] = (hash_type(raw), raw)
        # not elif: an embit that added taproot_key_sig while still writing
        # final_scriptwitness would otherwise stop this reading the witness
        key_sig = getattr(inp, "taproot_key_sig", None)
        if key_sig is not None:
            raw = bytes(key_sig)
            found[(i, "taproot_key", b"")] = (hash_type(raw), raw)
        if inp.final_scriptwitness and inp.final_scriptwitness.items:
            raw = bytes(inp.final_scriptwitness.items[0])
            found[(i, "witness", b"")] = (hash_type(raw), raw)
    return found


def sign_with(psbt, key):
    """Sign with the hash type the PSBT asks for, and hold the result to it.

    Raises PSBTRefusedError when there is no single type to describe the
    transaction, PSBTSignError when signing fails or produces something other
    than what the review screen promised.
    """
    shown_sighash, refused_because = screen_sighash_type(psbt, key)
    if shown_sighash is None:
        raise PSBTRefusedError(refused_because)

    requested = sighash_type(psbt)

    # snapshot the signature fields, since signing mutates the inputs in place
    # and a raise partway through would otherwise leave a half-signed PSBT.
    # Not a serialize/parse round trip: in compressed mode the spent output is
    # held privately with no previous transaction, so re-reading falls back to
    # the witness_utxo, which is exactly the field a coordinator can lie in.
    restore = [
        (
            inp,
            inp.partial_sigs.copy(),
            inp.taproot_key_sig,
            inp.final_scriptwitness,
            inp.sighash_type,
        )
        for inp in psbt.inputs
    ]
    before = signed_hash_types(psbt)

    try:
        sigs_added = psbt.sign_with(key.root, sighash=requested)

        # read back off the signatures rather than predicted, since sign_with
        # also signs inputs it matches by finding the root key in a script.
        # Compared by bytes, not by slot, so a host cannot plant a signature in
        # a slot we are about to fill.
        added = {
            where: hash_type
            for where, (hash_type, raw) in signed_hash_types(psbt).items()
            if before.get(where, (None, None))[1] != raw
        }
        if sigs_added == 0 or not added:
            raise PSBTSignError("cannot sign")
        if set(added.values()) - {shown_sighash}:
            raise PSBTSignError(
                "signed hash types %s do not match the 0x%02x shown to the user"
                % (sorted(set(added.values())), shown_sighash)
            )
    except PSBTSignError:
        _restore_signatures(restore)
        raise
    except Exception as e:  # pylint: disable=W0703
        _restore_signatures(restore)
        raise PSBTSignError(str(e))


def _restore_signatures(restore):
    """Puts the signature fields back the way the coordinator sent them"""
    for inp, partial_sigs, taproot_key_sig, witness, sighash_type_ in restore:
        inp.partial_sigs = partial_sigs
        inp.taproot_key_sig = taproot_key_sig
        inp.final_scriptwitness = witness
        inp.sighash_type = sighash_type_
