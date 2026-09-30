# Building and testing KruxMobileApp

This app has no test suite, no CI and no `poe` tasks, so the checks that exist
are the ones in `tools/`. This document is what they are, what each one needs,
and how to build an APK.

Everything here runs on a Linux host. No check requires the phone; only the
device tests in `DEVICE-TESTING.md` do.

## Contents

- [Layout](#layout)
- [Environment prerequisites](#environment-prerequisites)
- [Running the checks](#running-the-checks)
- [What each check covers, and what it does not](#what-each-check-covers-and-what-it-does-not)
- [Building the APK](#building-the-apk)
- [The upstream Krux test suite](#the-upstream-krux-test-suite)
- [A note on Docker vs podman](#a-note-on-docker-vs-podman)

## Layout

```
mocks/            the MicroPython compatibility layer: board, sensor, lcd, ujson,
                  uUR, base32, deflate, baseconv, ... Each re-registers itself
                  under its bare name so src/krux can import it as a builtin
src/krux/         vendored Krux, plus 20 recorded Android modifications
vendor/embit/     submodule, pinned to the tag v0.8.1-unified-sighash.1
tools/            everything below
  krux_delta.py         the sync gate: is src/krux upstream + our edits?
  krux-delta/           the 20 recorded patches, and a MANIFEST saying why
                        each file is modified
  patch_p4a.py          the five fixes for python-for-android's three defects,
                        plus an opt-in wheelhouse, re-applied per build
  verify_*.py           the checks described below
  test-stubs/           kivy/board/ujson stand-ins for the upstream suite
  krux_test_harness.py  pytest plugin for that suite
  run_krux_tests.py     runs it against this tree and a baseline
```

## Environment prerequisites

### For the checks

Only `tools/verify_*.py` and `tools/krux_delta.py` run on the host, and they
need almost nothing:

| need | for |
|---|---|
| Python 3.11+ | all of them |
| `numpy`, `pillow` | `verify_sensor_stats.py` (it compares against numpy) |
| a Krux checkout with its `.venv` | `verify_uur_shim.py`, `verify_qr_formats.py` |
| `qrcode` in that venv | `verify_qr_formats.py` (it imports `krux.qr`) |

`verify_uur_shim.py` needs the real `uUR` C extension, which only exists in a
Krux venv -- it is the oracle the shim is compared against. Krux's venv is
created with `uv sync` from the krux checkout:

```bash
cd ../krux
uv sync                    # creates .venv, with embit from the submodule
```

If `qrcode` is missing there, `uv pip install --python .venv/bin/python qrcode`.

`verify_base32.py`, `verify_mock_surface.py` and `krux_delta.py` need nothing
beyond the standard library.

### For the build

| need | for |
|---|---|
| `podman` or `docker` | the buildozer container |
| `git` | buildozer clones p4a |
| the `ghcr.io/kivy/buildozer:latest` image | ~1.3 GB, pulled once |

`java`, `cmake` and the Android SDK are **not** needed on the host: they are all
inside the image. `adb` is only needed to install and drive the app.

Verified on: Python 3.14.4, podman 5.7.0, git 2.55.0, adb 1.0.41, host Linux
5.15. No `uv` on this host, which is why the Krux venv step above assumes one.

## Running the checks

Run them from the repository root. All six should pass before a sync is
considered good.

```bash
# 1. the sync gate. Is src/krux exactly upstream + our 20 recorded edits?
python3 tools/krux_delta.py verify unified-sighash-single-min-noknots

# 2. do the MicroPython imports src/krux makes have shims that supply them?
python3 tools/verify_mock_surface.py

# 3. does mocks/base32.py match the firmware's, and is it as strict?
python3 tools/verify_base32.py

# 4. do the shim's decoder states match the firmware's C module?
../krux/.venv/bin/python tools/verify_uur_shim.py --krux ../krux

# 5. can the real QR parser handle every format a PSBT arrives in?
PYTHONPATH=../qrcode-site-packages \
  ../krux/.venv/bin/python tools/verify_qr_formats.py

# 6. does the Pillow entropy statistic still equal the numpy one it replaced?
python3 tools/verify_sensor_stats.py
```

`verify_qr_formats.py` imports `qrcode` at module scope, so it needs that
package importable by the Krux venv's interpreter. The `PYTHONPATH` line above
assumes you have one somewhere; the alternative is to install it into the venv.

Each exits 0 on success and non-zero on failure, so they drop straight into CI
or a pre-commit hook.

## What each check covers, and what it does not

This is the part that matters, because the two QR faults that prompted these
checks were both in `mocks/`, and the symptom they produced — a camera stuck on
screen — was in neither repo.

| check | catches | does not catch |
|---|---|---|
| `krux_delta.py verify` | silent divergence in `src/krux`; an edit that was not recorded | anything outside `src/krux/` |
| `verify_mock_surface.py` | `src/krux` importing a MicroPython module that `mocks/` does not provide | a name nothing imports yet; any value |
| `verify_base32.py` | base32 diverging from RFC 4648, or becoming lenient where the firmware is strict | nothing on-device |
| `verify_uur_shim.py` | any of the 12 decoder state codes differing from the C module; the shim completing on a different frame than the firmware | behaviour of `qr.py` itself |
| `verify_qr_formats.py` | a format the real parser cannot handle: 29 cases over UR single/multi/replayed, all 12 BBQr encoding x filetype combinations, pMofN, plain base64 | anything requiring a camera |
| `verify_sensor_stats.py` | the entropy statistic drifting from numpy, on 14 cases including flat, two-value and wrong-size frames | nothing on-device |

The division of labour is deliberate. `mocks/` is a partial reimplementation of
firmware modules, and it drifted twice: once because a module was missing
entirely (`base32`), once because upstream started using new parts of an API the
shim had only partly implemented (the `uUR` decoder states). Both times the
fault was in `mocks/`, which `krux_delta.py` does not cover -- it only knows
about `src/krux`. The three mock-facing checks exist to close that.

Known limits, stated plainly:

- **No check verifies a shim's *values*** beyond base32 and the uUR states.
  A wrong constant in some other shim would pass all six.
- **No check runs on a device.** These are host-side. Device behaviour is
  `DEVICE-TESTING.md`, and it is manual.
- **`verify_mock_surface.py` tolerates 10 known gaps** -- `base43`, `flash`,
  and parts of `machine`, `fpioa_manager`, `uhashlib_hw` -- each with its reason
  recorded in the script. A gap that is *not* in that list fails the run. The
  list is not an excuse to ignore new gaps; it is what lets the check be green
  enough to be trusted when it is red.

## Building the APK

Three things must happen in order.

**1. Patch python-for-android.** p4a is cloned into `.buildozer` by buildozer
and is therefore a cache: a fresh clone, or `buildozer clean`, discards the
edits. Three upstream defects stop this project from building without them. The
script applies five fixes for those three defects, because two of them are the
same pip problem reached from different code paths:

| | defect |
|---|---|
| A | `lookup_prebuilt` raises on `sh`'s background thread instead of returning False, killing any package with no prebuilt Android wheel |
| B, D, E | pip is self-upgraded into a venv it is about to use -- B in the build venv, D in `create_venv`, and E with the *target* site-packages on `PYTHONPATH`, which is the one that actually caused the corruption |
| C | `run_pymodules_install` asks host pip to install Android wheel URLs, which host pip rejects |

Re-apply after any clean:

```bash
python3 tools/patch_p4a.py
```

A sixth patch, **F**, is not a fix for any of the three and is inert unless you
set `P4A_WHEELHOUSE`, in which case pip resolves from that directory and uses no
index at all. It exists because a container can reach an index so slowly that the
fetch stalls rather than fails, which looks like a slow build rather than a
broken one — compare host and container timings on the same URL before blaming a
recipe. With the variable unset the option list is what upstream passes, and a
full build was verified that way.

It is idempotent, prints what it did, and asserts that p4a's HEAD is on
`master`. That last part matters: on a detached HEAD, buildozer deletes and
re-clones p4a mid-build, which silently discards every patch. Details and
upstream-ready write-ups are in `UPSTREAM-P4A-BUGS.md`.

**2. Accept the Android SDK licence.** A fresh clone has no
`.buildozer/android/platform/android-sdk/licenses/`, and without it the SDK
manager silently declines to install build-tools and the build dies on

    # Check that aidl can be executed
    # build-tools folder not found .../android-sdk/build-tools
    # Search for Aidl
    # Aidl not found, please install it.

which names a symptom rather than the cause. The value is a well-known public
SDK licence hash:

```bash
mkdir -p .buildozer/android/platform/android-sdk/licenses
printf '\n24333f8a63b6825ea9c5514f83c2829b004d1fee\n' \
  > .buildozer/android/platform/android-sdk/licenses/android-sdk-license
```

Unattended builds cannot answer the prompt, which is why the file has to be
written. Once written it survives as long as the SDK directory does.

**3. Run buildozer.** `android_build.sh` is upstream's and uses `docker`:

```bash
./android_build.sh
```

On a host where the docker socket is inaccessible, use podman instead. Two
differences from the script: `--privileged` is required, and buildozer prompts
for confirmation when running as root, which needs an answer on stdin:

```bash
printf 'y\n' | podman run --rm --privileged -i \
  -v "$PWD":/home/user/hostcwd \
  -v "$PWD/.buildozer":/home/user/.buildozer \
  -v "$PWD/.gradle":/home/user/.gradle \
  -w /home/user/hostcwd \
  ghcr.io/kivy/buildozer:latest \
  android debug
```

Output lands in `bin/`. A warm build — the 9.3 GB `.buildozer` already populated
— takes **about 1m45s**. A cold one is 40–60 minutes, most of it the SDK and NDK
download. Retaining a downloaded archive does *not* skip the download: buildozer
re-fetched a 689 MB NDK zip that was sitting right there, because p4a only
consults its own extracted directory.

If the index is slow rather than down, the build will stall instead of failing,
which looks like progress. `P4A_WHEELHOUSE` makes pip resolve from a local
wheelhouse with no index at all (see `patch_p4a.py`); compare a host and a
container fetch of the same URL before blaming a recipe.

Do not rename `.buildozer/android/platform/build-*` between builds. The path is
baked into the host `python3`'s `pip3` shim, and renaming it produces
`ErrorReturnCode_127` during the next build with no indication of the cause.

Container artefacts are root-owned and cannot be removed with `rm`. Use a
throwaway container:

```bash
podman run --rm -v "$PWD":/w --entrypoint sh alpine -c 'rm -rf /w/.buildozer'
```

### Installing

```bash
adb install -r bin/krux-*.apk
```

Debug builds are signed with a keystore that buildozer generates and does not
keep. If you reinstall over a build from a different tree, or a different
machine, the signatures will not match and the install fails with
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`. `adb uninstall selfcustody.github.io.krux`
fixes it and **deletes the app's data, including any stored mnemonic**.

### Check the APK, not the build's report

The filename encodes the version, not the contents. Two things have to be read
out of the artefact itself.

**Architectures.** A 32-bit device needs `armeabi-v7a`, and the presence of
`arm64-v8a` in the name does not imply it:

```bash
unzip -l bin/krux-*.apk | grep -oE 'lib/[a-z0-9_-]+/' | sort -u
```

**The Python payload.** The `.pyc` files are packed into `assets/private.tar`
inside the APK, so the bundle is where a shim fix either is or is not:

```bash
unzip -p bin/krux-*.apk assets/private.tar | tar -t | grep -E 'base32|uUR|sighash'
```

Both shim bugs on this project were invisible to every gate in `tools/` and
would have shipped as "the camera hangs with no way out". They were caught by
looking in here, and confirmed by a device.

## The upstream Krux test suite

`src/krux` is vendored Krux, so it is checked by running Krux's own suite
against it. `tools/run_krux_tests.py` builds a harness that holds everything
else fixed -- the same `tests/` and `simulator/`, the same venv, the same
`embit`, the same stubs -- and runs it twice, once against this tree and once
against a baseline ref, then reports how many failures the Android delta
explains.

```bash
python3 tools/run_krux_tests.py --krux ../krux
```

**It is not a gate, and cannot be made into one.** Upstream's suite tests two
platforms; this app is one of them. Krux's device fixtures report a Krux device,
so the app is tested against hardware it never runs on, and forcing the board
type to fix that breaks 540 geometry tests. The two sets are mutually exclusive.
`krux_delta.py verify` is the gate; this is informational, and its value is in
the unexplained-failure count.

Current: **185 failed / 1146 passed** on the app side. That is not 185 defects.
132 are the recorded Android delta, and most of the rest are the storage-shape
difference — the app's `encryption.py` uses a `JsonStore` where upstream uses a
dict and has no `_load_mnemonics`, so those tests fail on structure rather than
data, which no harness fixture can address.

**Memory is not a constraint, but the caps are load-bearing.** Three tests reach
`CameraEntropy.capture()`, whose `while True` waits for a button press the
fixture has already spent, while `unittest.mock` records every call made inside
the loop. Each passes 2.5 GB in under 30 seconds; together they reach ~12.5 GB,
which on a 15 GB host is the kernel OOM killer rather than a slow run. The
runner deselects them by name — **one `--deselect` per test**; a single flag with
three values honours only the first, which is how a run once reported "1
deselected" and then hit 13.5 GB. The plugin also caps peak RSS and interrupts
any test that overruns, but a per-test SIGALRM is the real backstop: a cap
checked *between* tests cannot help a test that is allocating continuously.

With those in place the suite is cheap: **~410 MB peak, 2m20s per side.**
Run it detached so a dropped shell does not lose it. Do not run it concurrently
with a device session — not because of memory, but because both drive the same
host hard enough to make timing-sensitive results untrustworthy.

`tests-harness.md` covers the harness in detail and the argument for why the
suite cannot be a blanket gate.

## A note on Docker vs podman

`android_build.sh` says `docker run --rm -it`. Where the docker socket is not
accessible to your user (`srw-rw---- root:docker`, and you are not in the
`docker` group), use podman, which works with `--privileged` and no daemon. The
`-it` also has to go in a non-interactive context, along with a `y` on stdin for
the root prompt.

Note that podman's `-v` binds do not map host UIDs the same way docker's do, so
files created in a bind mount can end up owned by a different user than the one
running podman. That is why the clean-up recipe above goes through a container.
