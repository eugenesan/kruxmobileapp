#!/bin/bash

mkdir -p ~/.buildozer $(pwd)/.buildozer
mkdir -p ~/.gradle $(pwd)/.gradle

# python-for-android is cloned into .buildozer by buildozer, and three upstream
# defects stop this project from building with the current image. They are
# patched from a script rather than edited in place, because .buildozer is a
# cache that a clean checkout or `buildozer clean` discards.
#
#   A  lookup_prebuilt raises on sh's background thread instead of returning
#      False, so any package without a prebuilt Android wheel kills the build
#   B  p4a upgrades pip into its own build venv without uninstalling, leaving
#      two pip versions mixed and every later pip call raising ImportError
#   C  run_pymodules_install asks host pip to install Android wheel URLs, which
#      host pip rejects as "not a supported wheel on this platform"
#
# Details and upstream-ready write-ups: UPSTREAM-P4A-BUGS.md
python3 tools/patch_p4a.py || exit 1

# Docker Build Script for Kivy Android
docker run --rm -it \
  -v $(pwd):/home/user/hostcwd \
  -v $(pwd)/.buildozer:/home/user/.buildozer \
  -v $(pwd)/.gradle:/home/user/.gradle \
  -w /home/user/hostcwd \
  ghcr.io/kivy/buildozer:latest \
  android debug
