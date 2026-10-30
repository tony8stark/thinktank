#!/bin/bash
# Check shipped OpenRouter defaults and keep one issue open while there are problems.
# Run from the repo root by .github/workflows/models.yml.
set -euo pipefail

title='Default OpenRouter model needs attention'
open_issue(){
  gh issue list --state open --search "$title in:title" --json number,title \
    --jq '.[] | select(.title == "Default OpenRouter model needs attention") | .number' | head -1
}

status=0
problems=$(THINKTANK_HEADS=/nonexistent python3 scripts/heads.py check-models) || status=$?
case "$status" in
  0)
    issue=$(open_issue)
    [ -z "$issue" ] || gh issue close "$issue" --comment 'All shipped OpenRouter models are current again.'
    echo 'Default OpenRouter models are current'
    exit 0;;
  1) ;;
  *) echo "Default OpenRouter model check failed (exit $status)" >&2; exit "$status";;
esac

body=$(printf 'The shipped OpenRouter heads need attention:\n\n```\n%s\n```\n\nReplace the id in scripts/heads.json, bump the version, and add a CHANGELOG entry.\n' "$problems")
issue=$(open_issue)
if [ -n "$issue" ]; then
  printf '%s\n' "$body" | gh issue edit "$issue" --body-file -
else
  printf '%s\n' "$body" | gh issue create --title "$title" --body-file -
fi
