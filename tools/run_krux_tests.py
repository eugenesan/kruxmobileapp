#!/usr/bin/env python3
"""Run Krux's own test suite against this app's src/krux, and against a baseline.

KruxMobileApp has no test suite of its own, and src/krux is a vendored copy of
Krux. So the app's copy is checked by running Krux's suite against it -- but
only if everything else is held fixed. This script builds a harness in which
it is, runs it twice, and compares:

  tests/ simulator/   extracted from a named Krux ref, byte for byte
  simulator uUR       replaced by the app's own mocks/uUR.py
  ur, urtypes         the app's copies, isolated from the krux tree
  kivy, board, ujson  stubs from tools/test-stubs
  interpreter         Krux's own .venv, so embit and the other path
                      dependencies resolve exactly as Krux intends
  harness plugin      tools/krux_test_harness.py

Run A puts the app's src/ on the path; run B puts the ref's src/krux/ there.
Same tests, same interpreter, same everything else. Whatever differs is
attributable to the krux package alone.

    python3 tools/run_krux_tests.py --krux ../krux
    python3 tools/run_krux_tests.py --krux ../krux --ref main --only baseline

A second run needs no separate invocation: the baseline is the reference for
the comparison, and failures are annotated with whether the file they live in
carries a recorded Android modification, read from tools/krux-delta/MANIFEST.

Exit status is 0 when the comparison completes, regardless of how many app
tests fail. It is not a pass/fail gate -- see tests-harness.md for why the
upstream suite cannot be one. Use tools/krux_delta.py verify for that.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(TOOLS)

# Three tests in the app tree reach CameraEntropy.capture(), whose `while True`
# waits for a button press the upstream fixture has already spent. mock records
# every call made inside that loop, so each one allocates without bound:
# test_encrypt_save_error_exist alone was measured at 4.9 GB in under 30 s, and
# the three together reach ~12.5 GB. On a 15 GB host that is not a slow run, it
# is the kernel OOM killer taking out unrelated processes -- which is how this
# was found. They are deselected in both runs so the two sides stay comparable.
#
# The per-test SIGALRM timeout in tools/krux_test_harness.py is the backstop,
# but it is not a substitute: these tests do finish, they just do it having
# allocated gigabytes on the way.
HANGING = [
    "tests/pages/test_encryption_ui.py::test_encrypt_save_error_exist",
    "tests/pages/test_encryption_ui.py::test_encrypt_save_error",
    "tests/pages/test_encryption_ui.py::test_encrypt_to_qrcode_ecb_ui",
]

SUMMARY = re.compile(r"^(\d+ (?:failed|passed)[^\n]*)$", re.M)
FAILED = re.compile(r"^FAILED (\S+)", re.M)


def die(msg):
    sys.exit("error: " + msg)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


# --- harness construction ----------------------------------------------------


def build(work, krux, ref):
    """Lay out the harness and return (harness_dir, baseline_srcdir)."""
    harness = os.path.join(work, "apptest")
    baseline = os.path.join(work, "baseline")

    for path in (harness, baseline):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(harness)
    os.makedirs(baseline)

    # tests/ and simulator/ straight out of the ref
    archive = subprocess.Popen(
        ["git", "-C", krux, "archive", ref, "tests", "simulator"],
        stdout=subprocess.PIPE,
    )
    extract = subprocess.run(["tar", "-x", "-C", harness], stdin=archive.stdout)
    archive.stdout.close()
    if archive.wait() or extract.returncode:
        die("could not extract tests/ and simulator/ from %s in %s" % (ref, krux))

    # the app's uUR shim, not the simulator's
    shutil.copyfile(
        os.path.join(APP, "mocks", "uUR.py"),
        os.path.join(harness, "simulator", "kruxsim", "mocks", "uUR.py"),
    )

    # baseline: the ref's src/krux, under a directory shaped like the app's
    # src/ so the two runs differ only in which krux/ they find.
    tmp = os.path.join(work, "extract")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    archive = subprocess.Popen(
        ["git", "-C", krux, "archive", ref, "src/krux"],
        stdout=subprocess.PIPE,
    )
    extract = subprocess.run(["tar", "-x", "-C", tmp], stdin=archive.stdout)
    archive.stdout.close()
    if archive.wait() or extract.returncode:
        die("could not extract src/krux from %s" % ref)
    shutil.move(os.path.join(tmp, "src", "krux"), os.path.join(baseline, "krux"))
    shutil.rmtree(os.path.join(tmp, "src"), ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    baseline_src = os.path.join(work, "baseline-src")
    if os.path.islink(baseline_src) or os.path.exists(baseline_src):
        os.remove(baseline_src)
    os.symlink(baseline, baseline_src)

    # A second layout for the baseline that mirrors the app's: a directory whose
    # name is `src` and which holds the krux package. Krux's tests import both
    # `krux.…` and `src.krux.…`, so the baseline needs both spellings, and its
    # `src.krux` has to be *its own* tree -- pointing this at the app would make
    # the baseline test the app and pass for the wrong reason.
    #
    # The nesting is load-bearing. pkg_root goes on PYTHONPATH, and the app's is
    # the repository root, so `src.krux` resolves through APP/src/krux. Putting
    # krux directly in baseline-pkg gave the bare `krux` spelling only, and the
    # three tests that import `from src.krux.…` -- test_xor_bytes,
    # test_fail_xor_bytes_different_lengths, test_rotary_encoder_handler -- failed
    # with ModuleNotFoundError: No module named 'src'. Same harness, same
    # interpreter, so it read as a sync defect; it was a missing directory level.
    baseline_pkg = os.path.join(work, "baseline-pkg")
    if os.path.islink(baseline_pkg) or os.path.exists(baseline_pkg):
        os.remove(baseline_pkg)
    baseline_pkg_src = os.path.join(baseline_pkg, "src")
    os.makedirs(baseline_pkg_src)
    os.symlink(os.path.join(baseline, "krux"), os.path.join(baseline_pkg_src, "krux"))

    # ur / urtypes from the app, in a directory of their own so that the
    # krux tree's own vendor/ copies cannot merge into the same package
    urprov = os.path.join(work, "urprov")
    shutil.rmtree(urprov, ignore_errors=True)
    os.makedirs(urprov)
    for name in ("ur", "urtypes"):
        os.symlink(os.path.join(APP, "src", name), os.path.join(urprov, name))

    # the harness plugin has to be importable by name
    for cache in (harness,):
        for dirpath, dirnames, _ in os.walk(cache):
            for d in list(dirnames):
                if d == "__pycache__":
                    shutil.rmtree(os.path.join(dirpath, d))
                    dirnames.remove(d)

    return harness, baseline_src, baseline_pkg


# --- running -----------------------------------------------------------------


def run_suite(work, harness, srcdir, label, python, extra, memcap_mb,
              include_hanging, pkg_root=None):
    cmd = [python, "-m", "pytest", "tests", "-q",
           "-p", "no:cacheprovider", "-p", "krux_test_harness",
           "--no-header", "-rf"]
    if not include_hanging:
        # One --deselect per test. `+= ["--deselect"] + HANGING` builds a single
        # flag with three values, and argparse honours only the first: the other
        # two become positional path arguments, so exactly one test is excluded
        # and the rest run. Those two reach CameraEntropy.capture() and allocate
        # until the process is killed, which is how this was found -- a run that
        # reported "1 deselected" and then hit 13.5 GB.
        for nodeid in HANGING:
            cmd += ["--deselect", nodeid]
    for item in extra:
        cmd += item.split()

    # In order: the tree under test, the app's ur/urtypes, the stubs, and
    # TOOLS so that `-p krux_test_harness` resolves by name.
    #
    # pkg_root, when given, is the directory *containing* the krux package's
    # parent. Two of Krux's test files import through a literal package prefix
    # -- `from src.krux.rotary import RotaryEncoder` -- which resolves only when
    # that directory is on the path. The app never does this; it is a test-side
    # convention, and without the entry those tests fail with
    # `ModuleNotFoundError: No module named 'src'`, which reads like a sync
    # defect and is not one.
    #
    # It must be per-side. Passing APP unconditionally would leave the baseline
    # resolving `src.krux` to the *app's* src, so the baseline would quietly
    # compare the app against itself and every such test would pass for the
    # wrong reason. The baseline gets a directory of its own that holds only its
    # own krux package.
    path = [srcdir, os.path.join(work, "urprov"),
            os.path.join(TOOLS, "test-stubs"), TOOLS]
    if pkg_root:
        path.insert(1, pkg_root)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(path)
    env["MEMCAP_MB"] = str(memcap_mb)

    print("== %s" % label)
    print("   krux from: %s" % srcdir, flush=True)
    proc = subprocess.run(cmd, cwd=harness, env=env, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    for line in out.splitlines():
        if re.search(r"(passed|failed|error|MEMORY CAP|peak RSS)", line):
            print("   " + line, flush=True)
    if proc.returncode not in (0, 1):
        print("   (pytest exit %d -- see below)" % proc.returncode)
        print("\n".join("   " + l for l in out.splitlines()[-25:]), flush=True)
    return proc.returncode, out


# --- comparison --------------------------------------------------------------


def summary(out):
    m = SUMMARY.search(out)
    return m.group(1) if m else "no summary line"


def failures(out):
    return {m.group(1) for m in FAILED.finditer(out)}


def modified_files():
    """Paths under src/krux that carry a recorded Android modification."""
    manifest = os.path.join(TOOLS, "krux-delta", "MANIFEST")
    paths = set()
    with open(manifest, encoding="utf8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line and "|" in line:
                paths.add(line.split("|", 1)[0].strip())
    return paths


def classify(app_failures, modified):
    """Split app failures into 'touches a recorded Android modification' or not.

    This is what turns "188 failures" into a statement about the delta rather
    than about the app. A failure in a file the MANIFEST does not list is the
    interesting one: either a real regression, or a gap in the recording.
    """
    android = {f for f in app_failures if _touches(f, modified)}
    return android, app_failures - android


def _touches(nodeid, modified):
    """Does this failing test live in a file that carries an Android delta?

    tests/pages/test_x.py -> pages/x.py, by dropping the leading "test_" and
    keeping the ".py" that is already there. Appending another one gave
    "display.py.py", which matches nothing, so every failure was reported as
    unexplained -- including the 21 in test_display.py, whose file *is* modified.
    A classifier that cannot attribute anything is worse than none: it reads as
    192 unexplained regressions when the truth is a handful.
    """
    if not modified:
        return False
    parts = nodeid.split("::", 1)[0].split("/")
    if parts[0] != "tests" or not parts[-1].startswith("test_"):
        return False
    leaf = parts[-1]
    if not leaf.endswith(".py"):
        return False
    rel = "/".join(parts[1:-1] + [leaf[len("test_"):]])
    return rel in modified


# --- main --------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="Run Krux's suite against this app and against a baseline ref.")
    ap.add_argument("--krux", required=True,
                    help="path to a Krux clone, with its .venv present")
    ap.add_argument("--ref", default="unified-sighash-single-min-noknots",
                    help="Krux ref to take tests/, simulator/ and the baseline from")
    ap.add_argument("--python", default=None,
                    help="interpreter (default: <krux>/.venv/bin/python)")
    ap.add_argument("--work", default=None,
                    help="harness directory (default: $TMPDIR/krux-harness)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the harness directory and print its path")
    ap.add_argument("--only", choices=["app", "baseline"], default=None)
    ap.add_argument("--memcap-mb", type=int, default=2500,
                    help="peak RSS ceiling (0 disables)")
    ap.add_argument("--include-hanging", action="store_true",
                    help="do not deselect the three known-hanging tests")
    ap.add_argument("pytest_args", nargs="*",
                    help="extra arguments passed through to pytest")
    args = ap.parse_args()

    krux = os.path.abspath(args.krux)
    if not os.path.isdir(os.path.join(krux, ".git")):
        die("--krux %s is not a git clone" % krux)
    python = args.python or os.path.join(krux, ".venv", "bin", "python")
    if not os.path.exists(python):
        die("no interpreter at %s -- create Krux's venv with `uv sync` in %s"
            % (python, krux))

    work = os.path.abspath(
        args.work or os.path.join(os.environ.get("TMPDIR", "/tmp"), "krux-harness"))
    os.makedirs(work, exist_ok=True)

    print("building harness in %s" % work)
    print("  ref:     %s" % args.ref)
    print("  python:  %s" % python)
    harness, baseline_src, baseline_pkg = build(work, krux, args.ref)
    print("  tests:   %d files" % len(
        [f for f in os.listdir(os.path.join(harness, "tests")) if f.endswith(".py")]))
    print()

    # pkg_root is the directory holding the package's parent, so that both
    # `krux.x` and `src.krux.x` resolve to the tree under test. Each side gets
    # its own; see run_suite.
    results = {}
    for label, srcdir, pkg_root in (
            ("app", os.path.join(APP, "src"), APP),
            ("baseline", baseline_src, baseline_pkg)):
        if args.only and label != args.only:
            continue
        rc, out = run_suite(work, harness, srcdir, label, python,
                            args.pytest_args, args.memcap_mb,
                            args.include_hanging, pkg_root=pkg_root)
        log = os.path.join(work, "log-%s.txt" % label)
        with open(log, "w", encoding="utf8") as f:
            f.write(out)
        print("   log: %s" % log)
        results[label] = (rc, out, failures(out))
        print()

    if len(results) == 2:
        app, base = results["app"][2], results["baseline"][2]
        print("== comparison")
        print("   app:      %s" % summary(results["app"][1]))
        print("   baseline: %s" % summary(results["baseline"][1]))
        print("   failed in both runs:      %d" % len(app & base))
        android, other = classify(app, modified_files())
        print("   app-only, Android delta:  %d" % len(android))
        print("   app-only, unexplained:    %d" % len(other))
        for nodeid in sorted(other):
            print("     %s" % nodeid)
        print()
        print("   NOT a gate. %d app failures is a known state, not a regression;"
              % len(app))
        print("   tools/krux_delta.py verify is the gate. See tests-harness.md.")

    if args.keep:
        print("\nharness kept at %s" % harness)


if __name__ == "__main__":
    main()
