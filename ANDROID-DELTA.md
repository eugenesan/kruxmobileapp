# Maintaining the Android delta across Krux updates

`src/krux` is upstream Krux plus 21 Android-modified files. Every upstream sync
means re-applying that delta, and the interesting part is not how to do the
merge — that is a `patch` or a rebase, and it fails loudly when it cannot be
done. The interesting part is the failure mode that *does not* announce itself.

## The failure mode this is designed to catch

A merge can succeed and still be wrong. `src/krux/encryption.py` is the worked
example: the Android change replaces the whole mnemonic storage backend, and it
was authored against an older upstream. Merging main in therefore succeeds, and
leaves the app on the **older** backend — so it silently forgoes main's
`StorageCorruptedError` corruption tolerance. Five references in the branch,
zero in the app. No conflict, no warning.

The general shape: a file the Android side owns wholesale, where a successful
merge means the app keeps old upstream code without anyone noticing.

## The approach

Three parts. Only the third is the interesting one.

1. **`tools/krux-delta/`** — the delta as 21 patch files, one per modified file,
   each `diff(upstream, app)`, plus a `MANIFEST` naming the file and why it is
   modified. The reason column is documentation: it is what a reviewer reads when
   a merge conflicts, and what tells you whether a conflict is acceptable.

2. **`tools/krux_delta.py`** — `record`, `verify`, `report`.

3. **`verify` in CI or before a release.** This is the part that earns its keep.
   It re-derives `upstream + recorded patches` and asserts the result equals
   `src/krux` byte for byte. Drift in either direction is a hard failure.

```bash
tools/krux_delta.py record krux-sighash-noknots-min   # snapshot the delta
tools/krux_delta.py verify krux-sighash-noknots-min   # assert no drift
tools/krux_delta.py report                           # what is modified, and why
```

Current state:

```
21 modified file(s), 399 insertions, 270 deletions
65 files byte-identical to the branch, 1 app-only (gpg_ui.py)
```

`verify` catches both directions: an unrecorded edit to a patched file, and a
patched file that no longer matches its patch. Tested both ways — it exits 1 on
an injected one-line change and 0 when clean.

## Why not the alternatives

**Keep the Android mods as commits on a branch and rebase.** This is the
conventional answer and it is good at the merge itself — git does 3-way merges
and `git rerere` remembers resolutions. What it does *not* do is tell you that
the result is semantically right. The `encryption.py` case rebases cleanly and
is still wrong. Worth doing *as well as* the verifier, because it captures
intent and conflict resolutions in history; not a substitute.

**Keep the 21 files as verbatim overrides copied over `src/krux` after each
sync.** Simple and conflict-free, and it is what I would argue *against*: it
discards upstream changes to those files wholesale, so a security fix landing in
`encryption.py` or `display.py` is silently dropped with no signal at all. The
failure is invisible and permanent.

**Do nothing and rely on the test suite.** The suite cannot do this job. Against
the app tree it reports 1143 passed / 188 failed, and all 188 trace to the Android
modifications. It is a useful regression net but it is not a merge checker: it
tells you behaviour changed, not whether the change was intended, and it cannot
distinguish "the Android mod is doing its job" from "upstream's fix got lost".
It cannot be made into a gate, either — `tests-harness.md` has the argument, and
it comes down to `AndroidStore` implementing only the Android half of the store
API, which makes upstream's write-then-read tests and its geometry tests mutually
exclusive.

## Suggested workflow for a sync

```bash
# 1. re-record against the new upstream ref
tools/krux_delta.py record krux-sighash-noknots-min

# 2. read the report; for every file whose delta changed size, read the patch
tools/krux_delta.py report
git diff --stat HEAD~1 -- tools/krux-delta

# 3. verify -- this is the gate
tools/krux_delta.py verify krux-sighash-noknots-min
```

If `verify` fails, the failure names the file and why. Two distinct messages:

- *delta no longer applies* — upstream changed the surrounding code. Re-apply the
  Android change by hand against the new upstream, then re-record.
- *has drifted* / *no recorded delta* — the tree and the delta disagree. Decide
  which is correct; if the tree is right, re-record, if not, restore it.

Then re-run the test suite (§ below) and the device tests
(`DEVICE-TESTING.md`).

## Running the upstream suite

`tools/run_krux_tests.py` runs Krux's suite against this tree and against a
baseline, and reports how many failures the Android delta explains. It needs a
Krux clone with its `.venv`; see `tests-harness.md` for what it needs and why
it is not a gate.

```
python3 tools/run_krux_tests.py --krux ../krux
```

It is **not** a gate, and cannot be made into one. `AndroidStore` in
`settings.py` implements only the Android half of the store API, so under
Krux's device fixtures `Setting.__set__` calls a `store.delete` that does not
exist, nothing persists, and every write-then-read test fails. Forcing
`board.config["type"]` to `android` fixes those and breaks 540 geometry tests —
the two sets are mutually exclusive, because upstream's suite tests two
platforms and this app is one of them. `verify` above is the gate; this is
informational, and its value is in the unexplained-failure count.

**The gap worth closing:** there is no automated test for the Android delta
itself. A focused suite over the 21 modified files — the clipboard path, the
`JsonStore` round trip, the QR version cap — is a few dozen tests and would be
worth more than the ~1143 that do pass. `tools/verify_sensor_stats.py` covers
one of them today.

## Known divergence, documented rather than fixed

`src/krux/encryption.py` carries an older mnemonic-storage backend than main's,
because the Android change replaces the storage layer wholesale. The app
therefore does not get main's `StorageCorruptedError` corruption tolerance.

This is a real gap in behaviour, not a merge error. The fix is to port main's
storage rework into the Android backend; it is not a sync task.

## Re-syncing embit by tag

Documents here and in the other notes refer to embit by **tag**
(`v0.8.1-unified-sighash.1`), never by commit SHA. A gitlink in an index is unavoidably a SHA, but
nothing a human reads needs to be, and a rebase of the embit fork moves every
SHA downstream without changing what the tag means.

To move both Krux and this app onto a newer embit:

```bash
# see what the tag currently points at
git -C vendor/embit fetch --tags
git -C vendor/embit describe --tags

# check it out, then commit the moved gitlink
git -C vendor/embit checkout v0.8.1-unified-sighash.1
git add vendor/embit && git commit -m "vendor/embit: move to v0.8.1-unified-sighash.1"
```

Then confirm Krux's suite still passes against the new pin before pushing
either repository.

## The outstanding blocker

`vendor/embit` is pinned to the commit tagged `v0.8.1-unified-sighash.1`. That
commit is on no remote under the names the upstream `.gitmodules` files used:

```
git ls-remote .../odudex/embit.git              | grep -c v0.8.1-unified-sighash.1 -> 0
git ls-remote .../privkeyio/embit.git          | grep -c v0.8.1-unified-sighash.1 -> 0
git ls-remote .../diybitcoinhardware/embit.git | grep -c v0.8.1-unified-sighash.1 -> 0
```

Both `.gitmodules` files now name `eugenesan/embit` — this repository with an
absolute URL, Krux with a relative one, matching each repo's own style — on all
eight Krux sighash branches. Krux's `main` and `feat/silent-payments` are
untouched; they pin different embit commits that do exist upstream.

**Still blocked:** the branch has to be pushed to that fork for either pointer
to resolve. Until it is, a fresh clone cannot obtain embit and cannot build,
and the `verify` step cannot be run by anyone else. The local clone at
`../embit` has the tag.
