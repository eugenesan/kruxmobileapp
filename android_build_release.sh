#!/bin/bash

# Only the project-local directories, because those are the ones mounted below.
# The container's ~/.buildozer and ~/.gradle are bind mounts of these, so creating
# the home-directory equivalents would leave two empty directories that no build
# ever reads. docker_bash.sh is the exception: it is an interactive shell and
# deliberately uses the home cache, so it creates nothing itself and needs the
# directory to exist.
mkdir -p "$(pwd)/.buildozer"
mkdir -p "$(pwd)/.gradle"

# Load environment variables
source kruxapp.env

# Same python-for-android patches as android_build.sh, from the same script, so
# the two builds cannot drift apart. The release build links the same native
# libraries and so hits the same upstream defects; android_build.sh says what
# they are and UPSTREAM-P4A-BUGS.md has the detail.
python3 tools/patch_p4a.py || exit 1

# Docker Build Script for Kivy Android
docker run --rm -it \
  -v "$(pwd)":/home/user/hostcwd \
  -v "$(pwd)/.buildozer":/home/user/.buildozer \
  -v "$(pwd)/.gradle":/home/user/.gradle \
  -w /home/user/hostcwd \
  -e P4A_RELEASE_KEYSTORE=/home/user/hostcwd/kruxapp-release-key.jks \
  -e P4A_RELEASE_KEYSTORE_PASSWD="$KEYSTORE_PASS" \
  -e P4A_RELEASE_KEYALIAS_PASSWD="$ALIAS_PASS" \
  -e P4A_RELEASE_KEYALIAS="$ALIAS_NAME" \
  ghcr.io/kivy/buildozer:latest \
  android release
