# The Krux test suite in this app

KruxMobileApp has no test suite, no CI, and no `poe` tasks. `src/krux` is a
vendored copy of Krux plus a recorded Android delta, so the question "is the
vendored copy still correct?" needs an answer, and this is how this repo gets
one.

There are two checks, and they answer different questions. Only one of them is
a gate.

## 1. Structural — `tools/krux_delta.py verify` — this is the gate

```
python3 tools/krux_delta.py verify unified-sighash-single-min-noknots
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

**Krux's device fixtures report a Krux device, so the app takes the upstream
branch against hardware it never runs on.** `Setting.__set__` in
`src/krux/settings.py` tests `board.config["type"] == "android"`. The fixtures
install `m5stickv`, `amigo`, `dock` and so on, so the condition is false and the
code falls through to the upstream path. Everything downstream of that is
tested against the wrong platform, and fails for that reason alone.

This is worth stating precisely, because the obvious reading of the failures is
wrong. The `AttributeError: 'AndroidStore' object has no attribute 'delete'`
does **not** mean the app's store is incomplete in production. `Store` defines
`delete` at `settings.py:257` and `update_file_location` at 276;
`AndroidStore` (from 311) defines neither. The `elif value == default:` branch
at line 101 that would call `store.delete` is only reachable when the board is
*not* Android, so on a real phone it never executes. The error appears only
because the fixtures put the app on the branch the app does not use.

Forcing `board.config["type"]` to `"android"` fixes those — and breaks 540
geometry tests, because the geometry code is exactly the code the Android delta
replaced. The two sets are mutually exclusive: there is no board configuration
that satisfies both. Upstream's suite tests two platforms at once, and the app is
only one of them.

### The numbers, and what they mean

```
app        185 failed, 1146 passed, 3 deselected, 1 xfailed, 1 error  139s
baseline    1332 passed, 3 deselected, 1 xfailed                        143s
```

The baseline's 3 failures were a missing `src/` directory level in the harness,
not a platform conflict — `from src.krux.…` resolved to nothing, so three tests
errored at import. The app's 4 were the inert
`patch("krux.encryption.open", …)`. So the baseline is clean, and the app's
count is 185 rather than 189.

Split by the MANIFEST, an earlier 189-failure run gave:

- **130** in files carrying a recorded Android modification
- **59** in files that do not, all downstream of the same fixture mismatch

**That split is stale.** Four of the 130 were in `test_encryption.py` and were
fixed, so it is now at most 126 / 59 — but which four, and whether anything else
moved, is not recorded, because that run's output was kept only as its tail.
`tools/run_krux_tests.py` re-derives the split from the MANIFEST on every run;
do that before quoting the numbers. **The totals above are verified; the split
is not.**

Those 59 are not one bug. Re-running them individually gives four distinct
signatures, all rooted in the board type being a Krux device:

| signature | count | root |
|---|---|---|
| `AttributeError: 'AndroidStore' object has no attribute 'delete'` | 24 | upstream branch, so writes are no-ops and validations never run |
| `draw_hcentered_text does not contain all of (call('below cave'…))` | 24 | the app's portrait-first layout differs from the geometry the tests assume |
| `<= not supported between int and MagicMock` | 14 | `display.py:322`, `_asian_chars_per_line()` returns a MagicMock because the numeric `lcd` the Android display reads is not installed |
| `save_file` / `print_string` / `write` not called | 13 | SD-card and printer paths that the app does not have |

**These four add to 75, not 59, and that discrepancy is not resolved.** The two
counts were taken from different runs and from different sets: the 59 excludes
failures in files the MANIFEST lists, and `display.py` *is* listed, so the 14
`MagicMock` failures may well belong in the 130 rather than the 59. Do not
present either table until it is re-derived from one run.

None of them is a regression from the sync, and none is fixable without lying to
the fixtures. The right disposition is to record them as expected rather than
chase them. The runner derives the split from the MANIFEST on every run, so the
number can be re-derived rather than taken on trust — and a *new* failure in a
file the MANIFEST does not list is the signal worth stopping for.

Two earlier claims in this file were wrong and are corrected here: the failures
were described as "187 of 188", and the cause was described as the app's store
being incomplete rather than the fixtures selecting the wrong branch. A third
followed: `test_load_encrypted_from_flash_wrong_key` was reported several times
as order-dependent, on the strength of having passed once in isolation. It was
never order-dependent — both sides collect the same tree in the same order — and
it passed in isolation in an *earlier* run, before the harness was rebuilt.

**Three tests allocate without bound, and one of them is fatal to the host.**
`test_encrypt_save_error_exist`, `test_encrypt_save_error` and
`test_encrypt_to_qrcode_ecb_ui` all reach `CameraEntropy.capture()`, whose
`while True` waits for a button press that the fixture's finite button sequence
has already spent. `unittest.mock` records every call made inside that loop, so
the hang is unbounded memory growth as well.

Measured individually, each of the three passes 2.5 GB in under 30 seconds. Run
together they reach ~12.5 GB, which on a 15 GB host is not a slow run but the
kernel OOM killer taking out unrelated processes. The runner deselects them by
name — **one `--deselect` per test**, because a single flag with three values
honours only the first, which is how a run once reported "1 deselected" and then
hit 13.5 GB. `tools/krux_test_harness.py` caps peak RSS and interrupts any test
that overruns as a backstop, though a per-test SIGALRM is the real one: a cap
checked *between* tests cannot help a test that allocates continuously.

With the deselect in place the whole suite peaks at **202 MB** and finishes in
about 2m20s per side, so the harness is cheap to run. The caps matter because the
failure mode is severe rather than because the suite normally needs the memory.

**These remain undiagnosed.** They are consistent with the Android flow reaching
an entropy wait that upstream does not, which would be a real behavioural
difference worth knowing about, but the harness cannot tell a fixture mismatch
from a real one. `DEVICE-TESTING.md` covers the manual check.

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
| `tools/krux_test_harness.py` | the pytest plugin: lcd geometry, settings and seeds resets, memory cap |
| `tools/test-stubs/` | `kivy.storage.jsonstore`, `board`, `ujson` |

The plugin replaced an earlier version that patched Krux's own `conftest.py`.
That was a bad shape: the patch depended on anchors in a file that changes
upstream, and a moved anchor meant a silently unpatched harness rather than an
error. All four accommodations are now hooks in a plugin — the lcd values are
set after `mp_modules` installs the mock, the two file resets are autouse
fixtures, and the seeds store is taught to read what a test injected — so the
harness never edits the code it is testing. That also means a change to Krux's
`conftest.py` cannot break it.

**The seeds accommodation is the one that is not obvious.** The app's
`MnemonicStorage` reads encrypted seeds from a real `JsonStore`; upstream reads
them from a dict built by calling `open`. So every test that seeds storage with
`patch("krux.encryption.open", mock_open(read_data=SEEDS_JSON))` patched a
function the app never calls — `grep -c 'open('` on the app's `encryption.py`
returns 0 — and the store came up empty, so assertions about its contents failed
against nothing. The store now reads the injection when it is *constructed*,
which is the only moment it is available: the test installs the patch inside its
own `with` block, later than any fixture could see. Four app-side tests that way
went from failing to passing, with no change to the tests.

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
