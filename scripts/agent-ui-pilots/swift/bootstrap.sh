#!/bin/sh
set -eu
cd "$(dirname "$0")"
revision=c4e0e069730eb265d7f6e81443a8394fa80ac3ad
checksum=73c01fc978f75dd220bc5b2eeaa3071b09213b609171f4785f91e73b62b2c4d0
mkdir -p .sdk
if [ -f .sdk/revision ] && [ "$(cat .sdk/revision)" = "$revision" ] && [ -f .sdk/a2ui/catalogs/basic/v1/catalog.json ]; then
    exit 0
fi
curl --fail --location --retry 2 --max-time 180 \
    "https://codeload.github.com/a2ui-project/a2ui/tar.gz/$revision" -o .sdk/a2ui.tar.gz
printf '%s  %s\n' "$checksum" .sdk/a2ui.tar.gz | shasum -a 256 -c -
mkdir -p .sdk/a2ui
tar -xzf .sdk/a2ui.tar.gz --strip-components 1 -C .sdk/a2ui \
    "a2ui-$revision/Package.swift" "a2ui-$revision/LICENSE" \
    "a2ui-$revision/swift" "a2ui-$revision/specification" "a2ui-$revision/catalogs"
printf '%s\n' "$revision" > .sdk/revision
rm .sdk/a2ui.tar.gz
