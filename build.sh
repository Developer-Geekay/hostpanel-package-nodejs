#!/usr/bin/env bash
set -euo pipefail

VERSION="$(python3 - <<'PY'
import re
from pathlib import Path
text = Path("plugin/setup.py").read_text()
match = re.search(r'version="([^"]+)"', text)
if not match:
    raise SystemExit("Could not find version in plugin/setup.py")
print(match.group(1))
PY
)"

ZIP="hostpanel-nodejs-${VERSION}.zip"

mkdir -p bin

for version in 22 24
do
  archive="sources/node-${version}-linux-arm64.tar.xz"
  if [ ! -f "$archive" ]; then
    echo "Missing runtime source archive: $archive" >&2
    exit 1
  fi
  tar -xOf "$archive" "node-${version}/bin/node" > "bin/node-${version}"
  chmod +x "bin/node-${version}"
done

for path in \
  bin/node-22 \
  bin/node-24
do
  if [ ! -x "$path" ]; then
    echo "Missing executable runtime asset: $path" >&2
    exit 1
  fi
done

rm -f "$ZIP"

zip -qr "$ZIP" \
  plugin \
  frontend \
  bin \
  conf \
  service \
  sudoers \
  README.md \
  test.scenario \
  -x "*.DS_Store" \
  -x "*/.DS_Store" \
  -x "__pycache__/*" \
  -x "*/__pycache__/*" \
  -x "*.pyc" \
  -x "*/tmp/*" \
  -x "runtime-full/*" \
  -x "runtime-full/**" \
  -x "*.tgz" \
  -x "*.zip"

echo "Built $ZIP"
