"""Apply two fixes to the python-for-android checkout in .buildozer.

p4a is cloned into .buildozer by buildozer and is therefore a cache: a fresh
clone, or `buildozer clean`, throws these edits away. Keep them here as a script
so they can be re-applied, and run it after p4a has been cloned.

    python3 tools/patch_p4a.py

Both are real upstream defects, not workarounds for this project.

Fix A -- PyProjectRecipe.lookup_prebuilt raises instead of returning False.

    build_arch calls check_prebuilt first and is *meant* to fall through to a
    source build when no prebuilt Android wheel exists. lookup_prebuilt has the
    right try/except for that, but `sh` runs the probe on its own background
    thread and raises ErrorReturnCode_1 there, where the except cannot see it.
    The exception escapes, lands on an unhandled thread, and the build dies
    instead of building the package from source. This bit pycryptodome,
    camera4kivy and pyqrcode. `_fg=True` keeps the exit code synchronous, so
    the existing except does its job.

Fix B -- p4a upgrades pip into its own build venv without uninstalling first.

    install_hostpython_prerequisites runs

        pip install build[virtualenv] pip setuptools patchelf \
            --target <venv site-packages> --upgrade

    `pip install --target` does not uninstall the package it is replacing, so
    upgrading pip over itself leaves files from two pip versions mixed in the
    same site-packages. In practice: 25.3's exceptions.py sitting beside 26.x's
    installer.py, which imports BuildDependencyInstallError from it, so every
    later pip invocation dies with

        ImportError: cannot import name 'BuildDependencyInstallError'

    Dropping "pip" from the list leaves the venv's own pip alone. setuptools
    and build are still upgraded; nothing depends on pip being newest.
Fix C -- run_pymodules_install asks host pip to install android wheels.

    The module resolver records the URL of the wheel it resolved (build.py
    appends `module["download_info"]["url"]`), and run_pymodules_install writes
    those URLs into requirements.txt and runs

        venv/bin/pip install -v --target <site-packages> --no-deps -r requirements.txt

    For a package that has an Android wheel that URL points at an
    `android_24_arm64_v8a` wheel, which is a tag describing an *Android device*.
    Host pip runs on x86_64 Linux, so it refuses it:

        ERROR: charset_normalizer-3.5.1-cp314-cp314-android_24_arm64_v8a.whl
               is not a supported wheel on this platform.

    Older pip tolerated alien platform tags; current pip does not. The packages
    concerned have already been cross-compiled and installed by their recipes
    during the recipe phase ("Installing built wheel: ...android_24_arm64_v8a"),
    so re-installing them here is redundant as well as impossible. This drops
    those URLs from requirements.txt and lets the recipe-built copies stand.
"""
import os
import subprocess
import sys

P4A = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".buildozer", "android", "platform", "python-for-android",
)
RECIPE = os.path.join(P4A, "pythonforandroid", "recipe.py")
BUILD = os.path.join(P4A, "pythonforandroid", "build.py")
PYTHONPACKAGE = os.path.join(P4A, "pythonforandroid", "pythonpackage.py")

FIX_A_OLD = """            shprint(self._host_recipe.pip, *pip_options, _env=pip_env, silent=True)
        except Exception:
            return False
        return True"""

FIX_A_NEW = """            # _fg=True: sh otherwise raises the non-zero exit on its own
            # background thread, where the except below cannot see it, so a
            # package with no prebuilt Android wheel killed the build instead
            # of falling through to the source build in build_arch.
            shprint(self._host_recipe.pip, *pip_options, _env=pip_env,
                    silent=True, _fg=True)
        except Exception:
            return False
        return True"""

FIX_B_OLD = 'packages=["build[virtualenv]", "pip", "setuptools", "patchelf"]'
FIX_B_NEW = 'packages=["build[virtualenv]", "setuptools", "patchelf"]'

FIX_C_OLD = """            with open('requirements.txt', 'w') as fileh:
                for module in modules:
                    key = 'VERSION_' + module
                    if key in environ:
                        line = '{}=={}\\n'.format(module, environ[key])
                    else:
                        line = '{}\\n'.format(module)
                    fileh.write(line)"""

FIX_C_NEW = """            with open('requirements.txt', 'w') as fileh:
                for module in modules:
                    # Skip direct URLs to Android wheels. Host pip runs on the
                    # build machine, not on a device, so it rejects the
                    # android_24_arm64_v8a platform tag outright; and the
                    # package has already been cross-compiled and installed by
                    # its recipe, so installing it again here is redundant.
                    if "://" in module and "android_" in module:
                        info("Already cross-compiled by its recipe, skipping {}"
                             .format(module.rsplit("/", 1)[-1]))
                        continue
                    key = 'VERSION_' + module
                    if key in environ:
                        line = '{}=={}\\n'.format(module, environ[key])
                    else:
                        line = '{}\\n'.format(module)
                    fileh.write(line)"""

FIX_D_OLD = """                "install", "-U", "pip", "wheel",
            ])"""

FIX_D_NEW = """                # Do not upgrade pip here. Upgrading pip inside the venv it is
                # running from is what corrupts the venv (fixes B and E cover
                # the other two sites): the venv ends up with two pip versions
                # mixed in site-packages, after which every pip invocation
                # raises
                #   ImportError: cannot import name 'BuildDependencyInstallError'
                # The venv's pip comes from ensurepip and matches its stdlib.
                "install", "-U", "wheel",
            ])"""

# The site that actually causes the corruption. `pip install -U pip` is run with
# PYTHONPATH pointing at the *target* (Android) site-packages, so pip imports
# modules out of the cross-compiled directory while installing itself into the
# host venv. Files get written from a mixture of the two trees.
FIX_E_OLD = """        # Prepare base environment and upgrade pip:
        base_env = dict(copy.copy(os.environ))
        base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)
        info('Upgrade pip to latest version')
        shprint(sh.bash, '-c', (
            "source venv/bin/activate && pip install -U pip"
        ), _env=copy.copy(base_env))
"""

FIX_E_NEW = """        # Prepare base environment.
        #
        # p4a used to run `pip install -U pip` here, with PYTHONPATH pointing at
        # the target (Android) site-packages. That combination corrupts the
        # build venv: pip imports modules out of the cross-compiled tree while
        # installing itself into the host venv, so the result is two pip
        # versions mixed in one site-packages -- e.g. pip-25.3.dist-info and
        # pip-26.2.1.dist-info side by side, with 25.3's exceptions.py written
        # over 26.x's build_env/. Every later pip call then fails with
        #   ImportError: cannot import name 'BuildDependencyInstallError'
        # The venv is created moments above by `hostpython -m venv`, whose
        # ensurepip seed is already consistent with its own stdlib, so there is
        # nothing to gain from upgrading it here.
        base_env = dict(copy.copy(os.environ))
        base_env["PYTHONPATH"] = ctx.get_site_packages_dir(arch)
"""

# Not a p4a defect, and not needed for a build with a working network.
#
# A container can reach an index so slowly that the fetch stalls rather than
# fails, and a stalled index is indistinguishable from a slow build: the
# requests just never return. It was seen here with podman reaching pypi at
# ~240 s per request while the host fetched the same URL in 0.4 s, which is
# worth knowing about independently -- if a build ever hangs on a download,
# compare host and container timings before blaming the recipe.
#
# Setting P4A_WHEELHOUSE to a directory of wheels and sdists makes pip resolve
# from there with no index at all. That is also a useful assertion in its own
# right: the build either completes from the wheelhouse or names the package it
# could not find, where an unreachable index just hangs.
#
# Applied only when the variable is set, so with it unset the option list is
# byte-for-byte what upstream passes. Verified: a full build with it unset
# completed with no --no-index and no extra fetches.
FIX_F_OLD = """        # add platform tags
        tags = PyProjectRecipe.get_wheel_platform_tags(arch.arch, self.ctx)
        for tag in tags:
            opts.append(f"--platform={tag}")
"""
FIX_F_NEW = """        # add platform tags
        tags = PyProjectRecipe.get_wheel_platform_tags(arch.arch, self.ctx)
        for tag in tags:
            opts.append(f"--platform={tag}")

        # Resolve from a local wheelhouse when one is given, and from nowhere
        # else. Inert otherwise: see FIX_F in tools/patch_p4a.py for why this
        # exists and when to reach for it.
        wheelhouse = environ.get("P4A_WHEELHOUSE")
        if wheelhouse:
            opts.extend(["--no-index", "--find-links", wheelhouse])
"""


def patch(path, old, new, label, anchor_check, count=1):
    """Apply one replacement, reporting whether it landed or was already there."""
    src = open(path, encoding="utf8").read()
    if anchor_check in src:
        print("  %s: already applied" % label)
        return 0
    if old not in src:
        print("  %s: ANCHOR NOT FOUND -- p4a changed, review manually" % label)
        return 1
    hits = src.count(old)
    if hits != count:
        print("  %s: expected %d occurrence(s), found %d -- review manually"
              % (label, count, hits))
        return 1
    open(path, "w", encoding="utf8").write(src.replace(old, new))
    print("  applied %s (%d sites)" % (label, hits))
    return 0


P4A_URL = "https://github.com/kivy/python-for-android.git"


def sync_checkout():
    """Make sure the p4a checkout exists and sits on origin/master.

    buildozer clones p4a into .buildozer, but only when the directory is
    missing, and it resets nothing when it is already there. Relying on that
    makes the outcome depend on whatever happened to be left over, so reset it
    explicitly: clone if absent, otherwise force back to origin/master. That
    discards a patch from a previous run, which is fine because the patches are
    re-applied immediately afterwards and the script is idempotent.
    """
    if not os.path.isdir(P4A):
        print("  cloning p4a into %s" % P4A)
        os.makedirs(os.path.dirname(P4A), exist_ok=True)
        r = subprocess.run(
            ["git", "clone", "-b", "master", "--single-branch", P4A_URL, P4A],
            capture_output=True, text=True)
        if r.returncode != 0:
            print("  clone failed:\n%s" % r.stderr.strip()[:400])
            return 1
        return 0

    r = subprocess.run(["git", "-C", P4A, "fetch", "origin", "master"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  fetch failed:\n%s" % r.stderr.strip()[:400])
        return 1
    # -B master, not a detached checkout of origin/master. buildozer reads
    # `git branch -vv` and deletes and re-clones p4a when the second field is not
    # the branch it expects. A detached HEAD reports "(HEAD", which trips that
    # check, so the patches applied below would be thrown away before p4a ran.
    r = subprocess.run(["git", "-C", P4A, "checkout", "-f", "-B", "master",
                        "origin/master"], capture_output=True, text=True)
    if r.returncode != 0:
        print("  checkout failed:\n%s" % r.stderr.strip()[:400])
        return 1

    branch = subprocess.run(["git", "-C", P4A, "branch", "-vv"],
                            capture_output=True, text=True).stdout.split()
    if len(branch) < 2 or branch[1] != "master":
        print("  HEAD is not on 'master' (%s) -- buildozer would re-clone p4a "
              "and discard these patches" % (branch[1] if len(branch) > 1 else "?"))
        return 1
    head = subprocess.run(["git", "-C", P4A, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    print("  p4a on branch master at %s" % head)
    return 0


def main():
    if "--no-sync" not in sys.argv:
        if sync_checkout():
            return 1

    if not os.path.exists(RECIPE):
        print("  p4a not found at %s" % P4A)
        return 1

    rc = 0
    rc |= patch(RECIPE, FIX_A_OLD, FIX_A_NEW,
                "A: lookup_prebuilt runs its probe in the foreground",
                "silent=True, _fg=True)")
    # p4a has two call sites, and the pip venv corruption comes back if either
    # one is missed -- so both must go.
    rc |= patch(RECIPE, FIX_B_OLD, FIX_B_NEW,
                "B: stopped self-upgrading pip",
                'packages=["build[virtualenv]", "setuptools", "patchelf"]',
                count=2)
    rc |= patch(BUILD, FIX_C_OLD, FIX_C_NEW,
                "C: run_pymodules_install skips Android wheel URLs",
                'if "://" in module and "android_" in module:')
    rc |= patch(PYTHONPACKAGE, FIX_D_OLD, FIX_D_NEW,
                "D: stopped self-upgrading pip in pythonpackage.create_venv",
                '"install", "-U", "wheel",')
    rc |= patch(BUILD, FIX_E_OLD, FIX_E_NEW,
                "E: stopped self-upgrading pip in the polluted-PYTHONPATH venv",
                "there is\n        # nothing to gain from upgrading it here.")
    rc |= patch(RECIPE, FIX_F_OLD, FIX_F_NEW,
                "F: pip resolves from the local wheelhouse when one is given",
                '--find-links", wheelhouse')
    return rc


if __name__ == "__main__":
    sys.exit(main())
