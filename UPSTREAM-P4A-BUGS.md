# python-for-android bug reports (ready to forward upstream)

Three defects in `kivy/python-for-android`, all found while building
KruxMobileApp with `ghcr.io/kivy/buildozer:latest`. Each one kills the build
outright. Workarounds are applied by `tools/patch_p4a.py`, which resets the
`.buildozer` checkout to `origin/master` and re-applies all three, so a clean
checkout or `buildozer clean` does not lose them.

- p4a commit: `58d21141` ("Merge pull request #3321 from kivy/release-2026.05.09")
- Buildozer image: `ghcr.io/kivy/buildozer:latest` (Python 3.14.4, pip 26.2.1)
- Toolchain: CPython 3.14.2, NDK r28c, `android.api = 32`, arm64-v8a + armeabi-v7a

A fourth issue is a python-for-android *packaging* gap rather than a defect, and
is listed at the end.

---

## 1. `PyProjectRecipe.lookup_prebuilt` raises instead of returning `False`

**Severity:** build-breaking for any package without a prebuilt Android wheel

### What happens

`PyProjectRecipe.build_arch` is written to prefer a prebuilt wheel and fall back
to a source build:

```python
def build_arch(self, arch):
    if self.check_prebuilt(arch, "skipping build_arch"):
        result = self.install_prebuilt_wheel(arch)
        if result:
            return
        warning("Failed to install prebuilt wheel, falling back to build_arch")
    build_dir = self.get_build_dir(arch.arch)
    ...
```

`lookup_prebuilt` has the try/except that makes that work:

```python
def lookup_prebuilt(self, arch):
    pip_options = self.get_pip_install_args(arch)
    pip_options.extend(["--dry-run", "-q"])
    pip_env = self.get_hostrecipe_env()
    try:
        shprint(self._host_recipe.pip, *pip_options, _env=pip_env, silent=True)
    except Exception:
        return False
    return True
```

But `sh` runs commands on its own background thread and raises
`ErrorReturnCode_1` there, where the surrounding `except` cannot catch it. The
exception escapes, lands on an unhandled thread, and the build dies instead of
building the package from source.

The probe is:

```
pip3 install pycryptodome==3.23.0 --ignore-installed --python-version 3.14.2 \
  --only-binary=:all: --no-deps \
  --platform=android_24_arm64_v8a --platform=android_24_aarch64 --dry-run -q
```

which exits non-zero for any package with no matching Android wheel.

Observed traceback:

```
sh/__init__.py:2720 in background_thread
sh/__init__.py:2411 in fn
sh/__init__.py:820  in handle_command_exit_code
sh.ErrorReturnCode_1:
  RAN: .../pip3 install pycryptodome==3.23.0 ... --only-binary=:all: ...
  STDOUT:
  ERROR: Could not find a version that satisfies the requirement pycryptodome==3.23.0 (from versions: none)
```

Packages that tripped it here: `pycryptodome`, `camera4kivy`, `pyqrcode`.

### Suggested fix

Run the probe in the foreground so the existing handler works:

```python
shprint(self._host_recipe.pip, *pip_options, _env=pip_env,
        silent=True, _fg=True)
```

Worth considering whether other `shprint` calls whose failure is expected should
be foreground too; this one is the only place found where a non-zero exit is the
*normal* path.

### Note

`from versions: none` in these messages is easy to misread as "pip has no index
access". It is not: pip is reachable, and there simply is no wheel matching the
requested platform tags.

---

## 2. p4a corrupts its own build venv by upgrading pip under a polluted PYTHONPATH

**Severity:** build-breaking; the most confusing of the three to diagnose

### What happens

`run_pymodules_install` creates the build venv and then upgrades pip in it:

```python
# Use our hostpython to create the virtualenv
host_python = sh.Command(ctx.hostpython)
with current_directory(join(ctx.build_dir)):
    shprint(host_python, '-m', 'venv', 'venv')

    # Prepare base environment and upgrade pip:
    base_env = dict(copy.copy(os.environ))
    base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)
    info('Upgrade pip to latest version')
    shprint(sh.bash, '-c', (
        "source venv/bin/activate && pip install -U pip"
    ), _env=copy.copy(base_env))
```

`base_env["PYTHONPATH"]` points at the **target** (Android) site-packages, and
`ctx.get_site_packages_dir(arch)` is where the cross-compiled packages are
installed. So the host pip, while installing itself into the host venv, is
importing modules out of the cross-compiled tree. The result is two pip
versions mixed into one `site-packages`.

Observed, in `.buildozer/android/platform/build-*/build/venv/lib/python3.14/site-packages`:

```
pip-25.3.dist-info/                      <- seeded by ensurepip
pip-26.2.1.dist-info/                    <- from the -U upgrade
pip/__init__.py                __version__ = "25.3"
pip/_internal/exceptions.py              no BuildDependencyInstallError
pip/_internal/build_env/                 present -- a 26.x-only directory
```

`build_env` exists only in pip 26.x; pip 25.3 has no such directory. The
timestamps show 25.3's `exceptions.py` written *after* 26.x's `build_env/`, so
the two trees interleaved during the upgrade. Every later pip call then dies:

```
ImportError: cannot import name 'BuildDependencyInstallError' from 'pip._internal.exceptions'
```

Reproducible from a clean venv, so it is not a caching artefact.

### Suggested fix

Drop the upgrade. The venv was created moments earlier by `hostpython -m venv`,
whose `ensurepip` seed is already consistent with its own stdlib, so there is
nothing to gain from upgrading it. If the upgrade is wanted for a reason, it
needs to run without the target `site-packages` on `PYTHONPATH`.

This is the workaround applied here: the `pip install -U pip` line is removed.

### A second, related site

`Recipe.install_hostpython_prerequisites` also lists `"pip"` among the packages
it installs, and runs:

```python
pip_options = [
    "install", *packages,
    "--target", self._host_recipe.site_dir, "--python-version",
    self.ctx.python_recipe.version,
    "--only-binary=:all:",
]
if force_upgrade:
    pip_options.append("--upgrade")
```

`pip install --target` does not uninstall the distribution it replaces, so
upgrading pip over itself there leaves files from two versions behind as well.
`pythonpackage.create_venv` has a third site doing `pip install -U pip wheel`
inside the venv.

All three are removed in this repository. Only the first is required to make the
build work; the other two are the same mistake in the same shape and are worth
fixing together.

### Note on diagnosis

This one is easy to misattribute. `--target` without an uninstall looks like the
obvious culprit and *is* wrong in principle, but removing it alone does not fix
the build: the corruption persisted until the `PYTHONPATH`-polluted
`pip install -U pip` was also removed. Anyone fixing this should check the venv
for duplicate `pip-*.dist-info` directories, which is the reliable signature.

---

## 3. `run_pymodules_install` asks host pip to install Android wheels

**Severity:** build-breaking once a package has an Android wheel

### What happens

After the recipes have run, p4a installs the remaining Python modules. The module
resolver records the URL of the wheel it resolved:

```python
processed_modules.append(module["download_info"]["url"])
```

and `run_pymodules_install` writes those URLs into `requirements.txt`:

```
camera4kivy
certifi
chardet
filetype
gestures4kivy
idna
requests
six
urllib3
https://files.pythonhosted.org/packages/29/cd/2b812ce.../charset_normalizer-3.5.1-cp314-cp314-android_24_arm64_v8a.whl
```

then runs it through the **host** pip:

```python
shprint(sh.bash, '-c', (
    "venv/bin/pip install -v --target '{0}' --no-deps -r requirements.txt"
).format(ctx.get_site_packages_dir(arch)), _env=copy.copy(env))
```

`venv/bin/pip` is the build machine's interpreter, on x86_64 Linux. The wheel
tag `android_24_arm64_v8a` describes an *Android device*, not the host, so pip
rejects it:

```
ERROR: charset_normalizer-3.5.1-cp314-cp314-android_24_arm64_v8a.whl is not a supported wheel on this platform.
```

Older pip tolerated alien platform tags in this position; current pip does not.

The package is not actually missing: the log shows p4a already cross-compiled
and installed it during the recipe phase, via its own `WheelFile` extraction
path rather than pip:

```
Installing built wheel: android-1.0-cp314-cp314-android_24_arm64_v8a.whl
Installing built wheel: cffi-2.0.0-cp314-cp314-android_24_arm64_v8a.whl
Installing built wheel: Kivy-2.3.1-cp314-cp314-android_24_arm64_v8a.whl
Installing built wheel: pillow-11.3.0-cp314-cp314-android_24_arm64_v8a.whl
Installing built wheel: pycryptodome-3.23.0-cp37-abi3-android_24_arm64_v8a.whl
```

So the entry is both impossible for host pip and redundant.

### Suggested fix

Two reasonable options:

- Skip direct URLs whose wheel is Android-tagged in `run_pymodules_install`,
  since the recipe has already installed those packages. This is the workaround
  applied here.
- Or install such wheels by extraction, the way `install_prebuilt_wheel` already
  does with `WheelFile`, instead of round-tripping them through host pip.

---

## 4. No recipe for pure-Python packages that publish no Android wheel

**Severity:** packaging gap, worked around locally

`pyqrcode` is pure Python and depends on nothing, yet cannot be built for
Android out of the box: it is not among p4a's bundled recipes, and it publishes
no Android wheels for any Python version, so neither the recipe path nor the
prebuilt path is available.

`p4a-recipes/pyqrcode/__init__.py` in this repository adds a `PythonRecipe` for
it, which is all that is required:

```python
from pythonforandroid.recipe import PythonRecipe

class PyQRCodeRecipe(PythonRecipe):
    version = "1.2.1"
    url = "https://files.pythonhosted.org/packages/37/61/.../PyQRCode-{version}.tar.gz"
    name = "pyqrcode"
    site_packages_name = "pyqrcode"
    depends = ["setuptools"]

recipe = PyQRCodeRecipe()
```

Not filed as a bug — it is a gap in recipe coverage, and the fix belongs in
p4a. Raising it in case a generic pure-Python fallback recipe would be
welcome; that would cover this and similar packages.

---

## Reproducing

From a clean checkout of this project:

```bash
./android_build.sh
```

The script applies the three patches via `tools/patch_p4a.py` and then runs
`buildozer android debug` in the Buildozer image. Removing the patch step
reproduces all three failures.
