# The Krux test suite in this app

KruxMobileApp has no test suite, no CI, and no `poe` tasks. `src/krux` is a
vendored copy of Krux plus a recorded Android delta, so the question "is the
vendored copy still correct?" needs an answer, and this is how this repo gets
one.

There are two checks, and they answer different questions. Only one of them is
a gate.

## 1. Structural — `tools/krux_delta.py verify` — this is the gate

```
python3 tools/krux_delta.py verify unified-sighash-noknots-min
```

Asserts, byte for byte, that `src/krux` is exactly the named Krux ref plus the
21 patches in `tools/krux-delta/`. It catches drift in both directions: an
unrecorded edit to a patched file, and a patched file that no longer matches its
patch. Exits non-zero on either.

This is the check that matters, because it is the one that can be *wrong* in a
detectable way. See `ANDROID-DELTA.md` for the `encryption.py` case that
motivated it: the merge succeeded, the file was neither the new upstream code
nor the intended Android change, and nothing said so. That class of failure is
invisible to any test that only exercises behaviour, because the file still
behaves the way the older backend behaved.

Run it after every sync. It needs nothing but Python and a Krux clone.

## 2. Behavioural — `tools/run_krux_tests.py` — this is *not* a gate

```
python3 tools/run_krux_tests.py --krux ../krux
```

Runs Krux's own suite twice — once against this app's `src/krux`, once against
a baseline extracted from a Krux ref — with everything else held fixed: the same
`tests/` and `simulator/`, the same interpreter and venv, the same `embit`, the
same `ur`/`urtypes`, the same stubs. The runner then reports how many failures
are shared, how many the Android delta explains, and how many are unexplained.
It exits 0 whenever the comparison completes.

It is not a gate, and the reason is structural rather than a matter of tidying
up. Upstream's suite cannot be made to run cleanly against the app tree,
because the app deliberately implements a different platform.

### Why the upstream suite cannot be a gate

**The app's settings store implements only the Android half of the store API.**
`AndroidStore` in `src/krux/settings.py` has no `delete` and no
`update_file_location`. Upstream's device fixtures install a *Krux* device, so
`board.config["type"]` is a Krux device type, `Setting.__set__` takes the
upstream branch, and it calls a `store.delete` that does not exist. Nothing
persists, and every write-then-read test fails.

Forcing the board type to `"android"` fixes those — and breaks 540 geometry
tests, because the geometry code is exactly the code the Android delta replaced.
The two sets are mutually exclusive: there is no board configuration that
satisfies both. Upstream's suite tests two platforms at once, and the app is
only one of them.

So the honest number is not "188 failures to fix". It is: 187 of those 188 are
in files that carry a recorded Android modification, and one
(`tests/pages/test_input.py::test_invalid_touch_delimiter`) is transitive
through `touch.py`, whose Android modification comments out the delimiter
bounds checks. The runner reports exactly this split, from the MANIFEST, so the
number can be re-derived rather than taken on trust.

**Three tests hang, and one of them eats 12 GB.** `test_encrypt_save_error_exist`,
`test_encrypt_save_error` and `test_encrypt_to_qrcode_ecb_ui` all reach
`CameraEntropy.capture()`, whose `while True` waits for a button press that the
fixture's finite button sequence has already spent. `unittest.mock` records
every call made inside that loop, so the hang is unbounded memory growth too:
roughly 12.5 GB, at which point the OOM killer takes out the shell. The runner
deselects them by name and `tools/krux_test_harness.py` caps peak RSS as a
backstop. **These have not been diagnosed.** They are consistent with the
Android flow reaching an entropy wait that upstream does not, which would be a
real behavioural difference worth knowing about, but the harness cannot tell a
fixture mismatch from a real one. `DEVICE-TESTING.md` covers the manual check.

## What the harness needs, and what it does not touch

`tools/run_krux_tests.py` needs a Krux clone with its `.venv` present — for
`tests/`, `simulator/`, the simulator's extra dependencies, and the `embit`
path dependency that Krux declares. Krux's own `uv sync` creates the venv. The
clone is a peer checkout; the runner takes `--krux <path>` and never assumes
where it is.

Three committed pieces make it work:

| file | what it is |
|---|---|
| `tools/run_krux_tests.py` | builds the harness, runs both sides, compares |
| `tools/krux_test_harness.py` | the pytest plugin: lcd geometry, settings reset, memory cap |
| `tools/test-stubs/` | `kivy.storage.jsonstore`, `board`, `ujson` |

The plugin replaced an earlier version that patched Krux's own `conftest.py`.
That was a bad shape: the patch depended on anchors in a file that changes
upstream, and a moved anchor meant a silently unpatched harness rather than an
error. Both patches are now hooks in a plugin — the lcd values are set after
`mp_modules` installs the mock, and the settings reset is an ordinary autouse
fixture — so the harness never edits the code it is testing. That also means a
change to Krux's `conftest.py` cannot break it.

`tools/verify_sensor_stats.py` is separate and smaller: it checks the Pillow
entropy statistics in `mocks/sensor.py` against the numpy implementation they
replaced, over 14 cases including the degenerate ones. It needs `numpy` and
`pillow`, neither of which is in the Android build, so it runs on the host. It
is a real gate — it exits non-zero on a mismatch — but only for that one file.

## What a sync should look like

```
python3 tools/krux_delta.py verify <new-ref>    # must pass; re-record if upstream moved
python3 tools/run_krux_tests.py --krux ../krux   # informational; read the split
python3 tools/verify_sensor_stats.py            # must pass
```

If `run_krux_tests.py` reports failures in files the MANIFEST does *not* list,
that is the signal worth stopping for: it is either a real regression or a
recording gap in `tools/krux-delta/`. Both need a decision; neither should be
absorbed silently.
