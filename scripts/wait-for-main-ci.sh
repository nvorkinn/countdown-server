#!/usr/bin/env bash
# Waits for the CI workflows that ran on a commit of main to pass, so a release never ships a commit whose CI is red.
#
# Release.yml runs this first. It succeeds once every workflow in REQUIRED has a finished run on SHA that passed, and
# fails as soon as one of them failed or has no run on SHA at all (a push starts them all at once, so one that is
# missing isn't coming). A run that is still going is waited for, until TIMEOUT_S passes. Runs from pull requests
# don't count: only what ran once the commit was on main.
#
# Workflows are matched by path, not name: CodeQL's default setup names its runs after the event ("Push on main").
#
# Environment: GH_TOKEN, REPO (owner/name), SHA, and optionally REQUIRED (workflow paths separated by newlines;
# default below), TIMEOUT_S (default 2700), INTERVAL_S (default 20).
set -euo pipefail

: "${REPO:?}" "${SHA:?}"
REQUIRED="${REQUIRED:-.github/workflows/tests.yml
.github/workflows/lint.yml
dynamic/github-code-scanning/codeql}"
TIMEOUT_S="${TIMEOUT_S:-2700}"
INTERVAL_S="${INTERVAL_S:-20}"

# A pushed commit's runs; CodeQL's default setup reports the event "dynamic"; the buttons add workflow_dispatch.
EVENTS='["push","dynamic","workflow_dispatch"]'
deadline=$((SECONDS + TIMEOUT_S))

while true; do
  runs="$(gh api --paginate "repos/$REPO/actions/runs?head_sha=$SHA&per_page=100" \
    | jq -s --argjson events "$EVENTS" '[.[].workflow_runs[] | select(.event | IN($events[]))]')"

  failed=()
  waiting=()
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    # The newest run of the workflow (a re-run keeps its id and replaces its result). Some runs' paths end in @ref.
    state="$(jq -r --arg name "$name" '
      [.[] | select((.path | sub("@.*$"; "")) == $name)] | max_by(.id)
      | if . == null then "missing"
        elif .status != "completed" then "running"
        elif (.conclusion | IN("success", "skipped", "neutral")) then "passed"
        else "failed \(.conclusion) \(.html_url)" end' <<<"$runs")"
    case "$state" in
      passed) ;;
      running) waiting+=("$name ($state)") ;;
      missing) failed+=("$name: no run on this commit") ;;
      *) failed+=("$name: ${state#failed }") ;;
    esac
  done <<<"$REQUIRED"

  if [ "${#failed[@]}" -gt 0 ]; then
    printf '::error::CI failed on %s, so there is no release:\n' "$SHA"
    printf '  %s\n' "${failed[@]}"
    exit 1
  fi
  if [ "${#waiting[@]}" -eq 0 ]; then
    echo "CI passed on $SHA: $(tr '\n' ' ' <<<"$REQUIRED")"
    exit 0
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    printf '::error::CI on %s did not finish within %ss:\n' "$SHA" "$TIMEOUT_S"
    printf '  %s\n' "${waiting[@]}"
    exit 1
  fi
  echo "Waiting for CI on $SHA: $(IFS=,; echo "${waiting[*]}")"
  sleep "$INTERVAL_S"
done
