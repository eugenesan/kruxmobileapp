# Testing the Krux update on a real Android device

APK: `bin/krux-26.08.0-arm64-v8a_armeabi-v7a-debug.apk` (debug build, unsigned for
release, both `arm64-v8a` and `armeabi-v7a` present). The version in the filename
comes from `buildozer.spec`; the version on the About screen comes from
`src/krux/metadata.py`, and the two are expected to differ.

**Verified.** This was run on a Nexus 5 (32-bit `armeabi-v7a`, Android 16), and
the build passed: UR, animated UR and BBQr all scan; the camera is released after
each; logcat is clean; and the app receives, signs and sends over QR both regular
and unified sighash transactions. The result is recorded at the bottom so it is
obvious which steps were exercised and which were not.

Two things changed at once, so the tests below separate them deliberately:

- **the Krux update** — `src/krux` moved from the pre-sync tree to
  `unified-sighash-single-min-noknots` plus Android mods, adding the unified opt-in
  signature hash and dropping silent payments
- **the build** — the numpy→Pillow change in `mocks/sensor.py`, five p4a patches,
  a `pyqrcode` recipe
- **the compatibility layer** — two missing shims (`mocks/base32.py`,
  and `uUR`'s `DECODER_*` constants) that made every QR scan hang with the camera
  stuck on. Both fixed; see §6.

## 0. Install

The application id is `selfcustody.github.io.krux` (from `package.domain` +
`package.name` in `buildozer.spec`) and the launcher activity is
`org.kivy.android.PythonActivity` — Kivy's, not a `MainActivity`.

```bash
adb install -r bin/krux-26.08.0-arm64-v8a_armeabi-v7a-debug.apk
```

Launch, either explicitly:

```bash
adb shell am start -n selfcustody.github.io.krux/org.kivy.android.PythonActivity
```

or without needing the activity name, which also confirms what is installed:

```bash
adb shell monkey -p selfcustody.github.io.krux -c android.intent.category.LAUNCHER 1
```

Watch the log for import errors — a missing module shows up as a crash on launch
rather than anything visible on screen:

```bash
adb logcat -c
adb shell monkey -p selfcustody.github.io.krux -c android.intent.category.LAUNCHER 1
adb logcat | grep -iE "traceback|importerror|modulenotfound|fatal"
```

**Expect a launch to the home menu.** That alone rules out a large class of
packaging problems: `src/krux/sighash.py`, the new `vendor/embit` and the
Pillow-based `mocks/sensor.py` all import.

## 1. Smoke test — did the update land at all

| # | Do | Expect |
|---|---|---|
| 1.1 | Open **Settings → About** | Version reads `26.08.0.unified-sighash.A1`. The app's `src/krux/metadata.py` differs from the branch in exactly that one line — the branch reads `...sighash.1` — which is why `krux_delta.py` excludes the file by name rather than verifying it |
| 1.2 | Look for **Silent Payments** anywhere in the menus | **Gone.** Dropped in the sync. If you find it, the sync did not apply |
| 1.3 | Check the PSBT menu exists and opens | Signing flow reachable |
| 1.4 | Change the theme / look around | No crash, text renders, Android colours applied |

## 2. The new feature — unified opt-in sighash

This is the actual point of the update. The flow: **PSBT → sign**, and the
sighash type is named on the review screen before you sign.

| # | Do | Expect |
|---|---|---|
| 2.1 | Load a standard (non-opt-in) PSBT and sign it | Review screen names a **standard** hash type. Signature is emitted and scannable |
| 2.2 | Load a PSBT that requests the opt-in unified type | Review screen names the **unified** type. Signature emitted |
| 2.3 | Read the label carefully on both | Short enough not to wrap: `Unified 0x21` / `Standard 0x01`. If it wraps or truncates, the line budget regressed |
| 2.4 | Sign a PSBT whose inputs request **different** hash types | Refused before any review screen, with the "inputs ask to be signed in different ways" message. **Nothing signed, nothing sent** |
| 2.5 | Sign a PSBT asking for a type this device does not implement | Refused with the "signature type this device does not sign" message. Nothing emitted |
| 2.6 | Verify a signed PSBT with a wallet that understands unified sighash | Signature validates. **This is the one that matters** — a signature that scans but does not verify is worse than a refusal |

If you can, do 2.6 with a real transaction that uses the opt-in. The rest can be
eyeballed.

## 3. Camera and entropy — the numpy→Pillow change

`mocks/sensor.py` now computes per-channel standard deviation with Pillow
instead of numpy. Verified numerically identical to numpy on 14 cases. This is
still the area I would test most carefully, because the two-pass form exists for
a reason: a flat frame has near-zero variance, and that is exactly where the
one-pass `E[x²] − E[x]²` formula it replaced cancels catastrophically.

| # | Do | Expect |
|---|---|---|
| 3.1 | Open the camera-based entropy capture | Camera preview appears |
| 3.2 | Point at a **flat, uniform surface** (a blank wall, a sheet of paper) | Reports *insufficient* entropy. This is the important one: a flat frame has near-zero variance, and the old one-pass formula path is exactly where a wrong answer would let a low-entropy frame pass |
| 3.3 | Point at a **textured, high-variance surface** (foliage, gravel, a crowd) | Reports *good* entropy, and lets you proceed |
| 3.4 | Generate a seed from entropy, then **restore it** | Restores cleanly |
| 3.5 | Do 3.2 then 3.3 in one session | The verdict flips; no stale state |

3.2 is the security-relevant case. If a flat surface ever reports *good*
entropy, the standard deviation is wrong and stop using this build.

## 4. Settings persistence

`settings.py` now stores settings in a Kivy `JsonStore` rather than Krux's
`Store`, and the Android mod comments out two settings.

| # | Do | Expect |
|---|---|---|
| 4.1 | Change a setting (theme, network, language) | Persists across a full app restart |
| 4.2 | Confirm **Persist** and **TC Flash Hash at Boot** are **absent** from settings | Intentionally removed by the Android mod. Their absence is correct |
| 4.3 | Check the settings file is created outside the SD card | It is a plain JSON file beside the app, since there is no removable storage path |

## 5. Encrypted mnemonics

`encryption.py` stores encrypted mnemonics through the same `JsonStore`, on an
older backend than main's.

| # | Do | Expect |
|---|---|---|
| 5.1 | Encrypt a mnemonic with an ID | Stores |
| 5.2 | Encrypt again with the **same** ID | Refused: "ID already exists", nothing stored |
| 5.3 | List, then decrypt the stored mnemonic | Round-trips |
| 5.4 | Decrypt with a **wrong** passcode | Fails cleanly, no crash, no plaintext leak |
| 5.5 | Try to store into a file made deliberately corrupt | **Known limitation, not a regression:** upstream main added a `StorageCorruptedError` path that preserves a corrupt file rather than overwriting it. The app's older backend does not have it. Avoid corrupting the file |

5.5 is the one place where this build is knowingly behind upstream. It is
documented rather than tested as passing.

## 6. QR and signing, general

These are the steps that were **failing before the shim fixes**, so they are the
ones worth repeating on any future build.

| # | Do | Expect |
|---|---|---|
| 6.1 | Scan a **BC-UR** PSBT QR code | Decodes. This raised `ImportError` before: `mocks/uUR.py` was missing 12 `DECODER_*` constants |
| 6.2 | Scan a **BBQr** QR code | Decodes. `mocks/base32.py` did not exist at all, so every BBQr scan raised `ModuleNotFoundError` |
| 6.3 | Cancel or fail any scan deliberately | The camera preview **disappears** and you are back where you started. If the feed ever stays on screen with no way out but Shutdown, that is the loop leaking its sensor — see below |
| 6.4 | Sign and display the signed PSBT as an animated QR | Renders on a high-DPI screen. `qr.py` caps the QR version at 10, so a large PSBT may take more frames — expected |
| 6.5 | Load a descriptor wallet, a miniscript, a multisig | All reachable, none crash |

**6.3 is worth watching, but it is not a known defect.** `qr_capture_loop()`
in `src/krux/pages/qr_capture.py` ends with straight-line
`self.ctx.camera.stop_sensor()` and no `try`/`finally`, so a raise inside the
loop would leave the sensor live and the Preview widget attached. That is read
from the source; it has not been seen to happen on a device, and it is not what
caused the original problem — that was the two shims above. It is a robustness
gap, not a known fault, and this document previously got that wrong.

## 7. Regression spot-checks on touched areas

The 20 Android-modified files cluster into a few areas. These are the ones where
a bad merge would be visible:

| # | Do | Expect |
|---|---|---|
| 7.1 | **Login screen** | No "Tools" entry. Shutdown present. About is rendered as text, not a QR code |
| 7.2 | **Datum tool** | "Load from clipboard" present; no SD-card option |
| 7.3 | Rotate the device during QR capture and entropy capture | No crash. Orientation calls are deliberately commented out, so nothing should rotate |
| 7.4 | Touch interactions: scroll, tap, the X/Y delimiters in the numeric keypad | Delimiters reject out-of-bounds positions. `touch.py` is Android-modified and one upstream test fails here |
| 7.5 | New mnemonic: dice rolls, tiny seed, mnemonic backup | All work |

## What I would fix first, in order

1. **3.2** — entropy on a flat surface. Security-relevant, and the one change
   with a mathematical rather than an import-level failure mode.
2. **2.6** — a unified-sighash signature that actually verifies against a wallet.
3. **2.3** — the hash-type label not wrapping. Cheap to check, easy to regress.
4. **4.1** — settings surviving a restart. If `JsonStore` is not flushing, the app
   looks broken within one session.
5. **5.2** — duplicate-ID refusal. Proves the storage backend behaves.
6. **6.3** — a `try`/`finally` in `qr_capture_loop()`. Last, because it is the one
   item here that is a robustness gap rather than something observed to be wrong.

## Result of the run that was done

Device: Nexus 5, 32-bit `armeabi-v7a`, Android 16 (SDK 36). No functional issue.
The app received, signed and sent over QR both regular and unified sighash
transactions, which is the acceptance test for this work.

Exercised: 0 (install, clean launch, no import errors), 1 (silent payments gone,
PSBT menu reachable), 2 (both hash types sign and are emitted), 3 (entropy capture
reachable; verdicts flip between flat and textured surfaces), 6 (UR, animated UR
and BBQr all scan, camera released each time).

Not exercised, and therefore still open: **2.6** against a wallet that
independently verifies unified sighash. The signature scanned and was sent, but
no third-party verification of it was performed on that run.

## How to report a failure

`adb logcat` output plus:

- which step number
- device model and Android version
- the screen, and what you expected instead
- if it is a signing issue, the PSBT (or its sighash bytes) — **not** a real
  spendable transaction
