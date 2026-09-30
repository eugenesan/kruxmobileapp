#!/bin/bash

# Only the project-local directories, because those are the ones mounted below.
# The container's ~/.buildozer and ~/.gradle are bind mounts of these, so creating
# the home-directory equivalents would leave two empty directories that no build
# ever reads. docker_bash.sh is the exception: it is an interactive shell and
# deliberately uses the home cache, so it creates nothing itself and needs the
# directory to exist.
mkdir -p "$(pwd)/.buildozer"
mkdir -p "$(pwd)/.gradle"

# python-for-android is cloned into .buildozer by buildozer, and three upstream
# defects stop this project from building with the current image. They are
# patched from a script rather than edited in place, because .buildozer is a
# cache that a clean checkout or `buildozer clean` discards. Five fixes cover
# the three defects, since two of them are the same pip problem reached from
# different code paths.
#
#   A      lookup_prebuilt raises on sh's background thread instead of returning
#          False, so any package without a prebuilt Android wheel kills the build
#   B, D   p4a upgrades pip into a venv it is about to use, without uninstalling,
#          leaving two pip versions mixed: B in the build venv, D in create_venv
#   E      the same again, but with the *target* site-packages on PYTHONPATH.
#          This is the one that actually caused the corruption here; the reliable
#          signature is duplicate pip-*.dist-info in the build venv
#   C      run_pymodules_install asks host pip to install Android wheel URLs,
#          which host pip rejects as "not a supported wheel on this platform"
#
# F is not an upstream defect: it makes pip resolve from $P4A_WHEELHOUSE when
# that variable is set, and does nothing when it is not, so a build with a
# working network is unaffected. See tools/patch_p4a.py.
#
# Details and upstream-ready write-ups for A-E: UPSTREAM-P4A-BUGS.md
# Full build and test instructions: BUILDING-AND-TESTING.md
python3 tools/patch_p4a.py || exit 1

# Docker Build Script for Kivy Android
docker run --rm -it \
  -v "$(pwd)":/home/user/hostcwd \
  -v "$(pwd)/.buildozer":/home/user/.buildozer \
  -v "$(pwd)/.gradle":/home/user/.gradle \
  -w /home/user/hostcwd \
  ghcr.io/kivy/buildozer:latest \
  android debug
