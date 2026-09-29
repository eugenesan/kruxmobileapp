#!/bin/bash

mkdir -p .buildozer
mkdir -p .gradle

# Load environment variables
source kruxapp.env

# Same python-for-android patches as android_build.sh. The release build links
# the same native libraries, so it hits the same three upstream defects; see
# android_build.sh for what they are and UPSTREAM-P4A-BUGS.md for the details.
python3 tools/patch_p4a.py || exit 1

# Docker Build Script for Kivy Android
docker run --rm -it \
  -v $(pwd):/home/user/hostcwd \
  -v $(pwd)/.buildozer:/home/user/.buildozer \
  -v $(pwd)/.gradle:/home/user/.gradle \
  -w /home/user/hostcwd \
  -e P4A_RELEASE_KEYSTORE=/home/user/hostcwd/kruxapp-release-key.jks \
  -e P4A_RELEASE_KEYSTORE_PASSWD="$KEYSTORE_PASS" \
  -e P4A_RELEASE_KEYALIAS_PASSWD="$ALIAS_PASS" \
  -e P4A_RELEASE_KEYALIAS="$ALIAS_NAME" \
  ghcr.io/kivy/buildozer:latest \
  android release
