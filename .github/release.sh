#!/bin/bash
# Publishes one GitHub Release for the version in plugin.json, with its CHANGELOG entry as the notes.
# Run from the repo root by .github/workflows/release.yml. Does nothing when that version is already released.
set -euo pipefail

version=$(python3 -c 'import json; print(json.load(open(".claude-plugin/plugin.json"))["version"])')
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "bad version in plugin.json: $version" >&2; exit 1; }
tag="v$version"

if gh release view "$tag" >/dev/null 2>&1; then
  echo "$tag is already released"
  exit 0
fi

notes=$(awk -v head="## $version " 'index($0, head) == 1 { on = 1; next } on && /^## / { exit } on && (seen || NF) { seen = 1; print }' CHANGELOG.md)
[ -n "${notes//[[:space:]]/}" ] || { echo "CHANGELOG.md has no entry for $version" >&2; exit 1; }

# The pushed commit, not an older one that set the version: GITHUB_TOKEN gets HTTP 403 creating a tag on a commit
# whose workflow files differ from main.
commit=$(git rev-parse HEAD)
printf '%s\n' "$notes" | gh release create "$tag" --target "$commit" --title "$tag" --notes-file -
echo "released $tag at $commit"
