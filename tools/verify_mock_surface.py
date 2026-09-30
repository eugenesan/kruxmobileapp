#!/usr/bin/env python3
"""Does every MicroPython module src/krux imports exist, and export what it wants?

`mocks/` is this app's MicroPython compatibility layer. It supplies board,
sensor, lcd, ujson, uUR, deflate, baseconv and the rest, each re-registering
itself under its bare name so that `src/krux` can import it as if it were a
builtin. Two of those were missing or incomplete, and neither was caught by
anything:

  * base32 did not exist at all, so every BBQr scan raised ModuleNotFoundError
  * uUR exported no DECODER_* constants and URDecoder had no .state, so every
    BC-UR scan raised ImportError

Both surfaced only on a phone. tools/verify_qr_formats.py catches them
behaviourally, but only for the code paths it drives -- and it was written
*after* the first failure, by someone who already knew to look.

This is the cheap complement: it reads the import statements in src/krux,
collects what is asked of each MicroPython-only module, and asserts the shim
supplies it. Deriving the list from the imports rather than hand-listing it is
the point -- when a Krux sync brings in a qr.py that uses a fourth constant,
this fails on the host instead of on a device.

Its scope is worth being exact about, because it is narrower than it first
looks. It checks the names src/krux *asks for by import*. It does not check:

  * names nothing currently imports. mocks/uUR.py supplies 12 DECODER_*
    constants because the C module has 12 and the shim is meant to mirror it;
    qr.py imports only 3. Deleting the other 9 passes here, and is only caught
    by verify_uur_shim.py below, which diffs against the C module directly.
  * values. A shim defining DECODER_OK = 99 passes.

So the two oracle tests are not a nicety, they are what covers the rest:

  tools/verify_uur_shim.py   drives the real C module and the shim side by side,
                             and requires all 12 names to match
  tools/verify_base32.py     checks the port against RFC 4648, strictness too
  tools/verify_qr_formats.py drives the real QRPartParser over 29 cases

Between the three, the base32 fault and the uUR fault each fail at least one
check; the surface check alone would have caught base32 and not uUR.

    python3 tools/verify_mock_surface.py
"""

import ast
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(APP, "src", "krux")
MOCKS = os.path.join(APP, "mocks")

# Provided by CPython, or by the app's own src/ (ur, urtypes, embit), or by
# Kivy. Matched as a prefix, because src/krux imports submodules -- embit.psbt,
# kivy.core.clipboard -- and a set of exact names reports all of those missing
# when the package itself is present and correct.
PROVIDED_PREFIXES = (
    "os", "sys", "io", "math", "re", "time", "json", "hashlib", "hmac", "zlib",
    "base64", "binascii", "struct", "itertools", "functools", "collections",
    "random", "string", "gc", "copy", "textwrap", "unicodedata", "typing",
    "dataclasses", "enum", "abc", "codecs", "secrets", "statistics", "bisect",
    "_thread",        # CPython builtin module
    "ur",            # the app's own vendored ur / urtypes
    "embit",         # vendor/embit
    "krux",
    "mocks",
    "kivy",          # Kivy, plus its submodules
    "Crypto",        # pycryptodome
    "PIL",           # pillow
    "qrcode",        # pyqrcode's sibling, a real PyPI package
    "camera4kivy", "pyzbar", "gestures4kivy", "pyqrcode",
    "android",       # pyjnius / the android runtime
    "jnius", "android_storage",
)

# Registered into sys.modules at import time by mocks/load_mocks.py rather than
# existing as a file, so there is nothing in mocks/ to check. board and sensor
# are MagicMocks and the live Sensor; pmu genuinely has no module and logs
# "No module named 'pmu'" at startup, which the app tolerates.
RUNTIME_REGISTERED = {"board", "sensor", "lcd", "pmu"}

# Gaps that are real but do not matter on Android, each with the reason. These
# are tolerated, not ignored: an unrecognised import still fails the run, which
# is the property that matters. Without this the check could never go green, and
# a check that is always red gets ignored, which is worse than no check.
#
# Keyed by (module, name) for INCOMPLETE, or (module, None) for MISSING.
TOLERATED = {
    ("base43", None):
        "imported inside a try/except in baseconv.py, and base43 support is "
        "datum-only; reached only when encoding to base43",
    ("flash", None):
        "firmware.py and fill_flash.py, i.e. Kboot firmware update. Not a "
        "phone operation, and those pages are not reachable from the Android menu",
    ("fpioa_manager", "fm"):
        "MaixPy pin muxing. Absent from the shim; kboard reports no light and "
        "the light path is skipped",
    ("machine", "I2C"): "MaixPy I2C; no I2C peripheral on a phone",
    ("machine", "PWM"): "MaixPy PWM; no PWM peripheral on a phone",
    ("machine", "SDCard"): "MaixPy SD card; the app uses kivy JsonStore instead",
    ("machine", "Timer"): "MaixPy timer; unused on this path",
    ("machine", "unique_id"): "MaixPy chip ID; unused on this path",
    ("uhashlib_hw", "pbkdf2_hmac_sha256"):
        "hardware-accelerated pbkdf2; the shim exposes the hashlib version",
    ("uhashlib_hw", "sha256"):
        "hardware SHA-256; the shim exposes the hashlib version",
}


def die(msg):
    sys.exit("error: " + msg)


def shim_module(name):
    """Return the path of the shim providing `name`, or None.

    Note the order: check for the ".py" file first. Testing for a bare
    directory name first reported every shim as MISSING, because a module is a
    file, not a directory -- `mocks/uUR.py` was reported absent 37 times over
    before this was fixed.
    """
    py = os.path.join(MOCKS, name + ".py")
    if os.path.isfile(py):
        return py
    pkg = os.path.join(MOCKS, name)
    if os.path.isdir(pkg) and os.path.isfile(os.path.join(pkg, "__init__.py")):
        return pkg
    return None


def collect_imports():
    """Walk src/krux for imports of bare names, and what they ask for.

    Returns {module: {names...}} for `from X import a, b` and {module: None}
    for a plain `import X`, which only needs the module to exist.
    """
    wanted = {}
    for root, _dirs, files in os.walk(SRC):
        if "__pycache__" in root:
            continue
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            try:
                tree = ast.parse(open(path, encoding="utf8").read(), path)
            except SyntaxError as e:
                die("could not parse %s: %s" % (path, e))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        wanted.setdefault(alias.name, set())
                elif isinstance(node, ast.ImportFrom):
                    # level > 0 is a relative import, i.e. inside krux itself
                    if node.level:
                        continue
                    if node.module:
                        wanted.setdefault(node.module, set())
                        for alias in node.names:
                            wanted[node.module].add(alias.name)
    return wanted


def supplied_by_shim(module):
    """Names a mocks/<module>.py defines at module level.

    For a package, the names come from its __init__.py, which is what the
    original `from . import x` produced.
    """
    path = shim_module(module)
    if path is None:
        return None
    if os.path.isdir(path):
        path = os.path.join(path, "__init__.py")
    tree = ast.parse(open(path, encoding="utf8").read(), path)
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def provided_elsewhere(module):
    root = module.split(".")[0]
    return (root in PROVIDED_PREFIXES or module in PROVIDED_PREFIXES
            or module in RUNTIME_REGISTERED)


def main():
    if not os.path.isdir(SRC):
        die("no src/krux at %s" % SRC)
    wanted = collect_imports()
    print("  src/krux imports %d distinct top-level modules" % len(wanted))
    print()

    checked = 0
    problems = []
    tolerated = []

    for module in sorted(wanted):
        if provided_elsewhere(module):
            continue
        if shim_module(module) is None:
            key = (module, None)
            entry = ("MISSING", module, "no mocks/%s" % module)
            (tolerated if key in TOLERATED else problems).append(
                entry + (TOLERATED.get(key, ""),))
            continue
        checked += 1
        supplied = supplied_by_shim(module)
        if supplied is None:
            continue
        asked = wanted[module] - {"*"}
        for name in sorted(n for n in asked if n not in supplied):
            key = (module, name)
            entry = ("INCOMPLETE", module, name)
            (tolerated if key in TOLERATED else problems).append(
                entry + (TOLERATED.get(key, ""),))

    if problems:
        print("  %-11s %-20s %s" % ("status", "module", "detail"))
        print("  " + "-" * 78)
        for status, module, detail, _ in problems:
            print("  %-11s %-20s %s" % (status, module, detail))
        print()
        print("  %d unmet import(s). Add the shim, or record why it is safe in"
              % len(problems))
        print("  TOLERATED in this file with the reason.")
    else:
        print("  every MicroPython import src/krux makes is satisfied")
    print()
    print("  scope: names imported by src/krux only. A name nothing imports")
    print("  yet, and any value, are covered by verify_uur_shim.py and")
    print("  verify_base32.py -- see this file's docstring.")

    if tolerated:
        print()
        print("  %d tolerated gap(s), each with a recorded reason:"
              % len(tolerated))
        for status, module, detail, reason in tolerated:
            print("    %-11s %-18s %-22s %s" % (status, module, detail, reason[:44]))

    print()
    print("  %d shim(s) checked, %d unmet, %d tolerated"
          % (checked, len(problems), len(tolerated)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
