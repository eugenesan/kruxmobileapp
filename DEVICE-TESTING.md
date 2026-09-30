# Testing the Krux update on a real Android device

APK: `bin/krux-26.06.0-arm64-v8a_armeabi-v7a-debug.apk` (debug build, unsigned for
release, both `arm64-v8a` and `armeabi-v7a` present).

Two things changed at once, so the tests below separate them deliberately:

- **the Krux update** — `src/krux` moved from the pre-sync tree to
  `unified-sighash-single-min-noknots` plus Android mods, adding the unified opt-in
  signature hash and dropping silent payments
- **the build** — the numpy→Pillow change in `mocks/sensor.py`, five p4a patches,
  a `pyqrcode` recipe

## 0. Install

The application id is `selfcustody.github.io.krux` (from `package.domain` +
`package.name` in `buildozer.spec`) and the launcher activity is
`org.kivy.android.PythonActivity` — Kivy's, not a `MainActivity`.

```bash
adb install -r bin/krux-26.06.0-arm64-v8a_armeabi-v7a-debug.apk
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
| 1.1 | Open **Settings → About** | Version reads `26.06.beta1` with the `Android v0.2` marker. This is the one file where the app deliberately keeps its own VERSION rather than main's |
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
instead of numpy. Verified numerically identical to numpy on 14 cases, but
**never executed on hardware**, so this is the area I would test most carefully.

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

| # | Do | Expect |
|---|---|---|
| 6.1 | Scan a PSBT QR code | Decodes |
| 6.2 | Sign and display the signed PSBT as an animated QR | Renders on a high-DPI screen. `qr.py` caps the QR version at 10, so a large PSBT may take more frames — expected |
| 6.3 | Scan an XQR / larger-format code | Decodes if the app supports it |
| 6.4 | Load a descriptor wallet, a miniscript, a multisig | All reachable, none crash |

## 7. Regression spot-checks on touched areas

The 21 Android-modified files cluster into a few areas. These are the ones where
a bad merge would be visible:

| # | Do | Expect |
|---|---|---|
| 7.1 | **Login screen** | No "Tools" entry. Shutdown present. About is rendered as text, not a QR code |
| 7.2 | **Datum tool** | "Load from clipboard" present; no SD-card option |
| 7.3 | Rotate the device during QR capture and entropy capture | No crash. Orientation calls are deliberately commented out, so nothing should rotate |
| 7.4 | Touch interactions: scroll, tap, the X/Y delimiters in the numeric keypad | Delimiters reject out-of-bounds positions. `touch.py` is Android-modified and one upstream test fails here |
| 7.5 | New mnemonic: dice rolls, tiny seed, mnemonic backup | All work |

## What I would fix first, in order

1. **3.2** — entropy on a flat surface. Security-relevant and the least-tested change.
2. **2.6** — a unified-sighash signature that actually verifies against a wallet.
3. **2.3** — the hash-type label not wrapping. Cheap to check, easy to regress.
4. **4.1** — settings surviving a restart. If `JsonStore` is not flushing, the app
   looks broken within one session.
5. **5.2** — duplicate-ID refusal. Proves the storage backend behaves.

## How to report a failure

`adb logcat` output plus:

- which step number
- device model and Android version
- the screen, and what you expected instead
- if it is a signing issue, the PSBT (or its sighash bytes) — **not** a real
  spendable transaction
