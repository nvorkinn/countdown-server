#!/usr/bin/env bash
# Run by the deploy workflows, on the runner: logs in to Vaultwarden as the deploy-bot user with the Bitwarden CLI and
# writes one <name>.env per entry of secrets-map.json's <target> into <outdir>, ready to be sent to the host.
# Usage: fetch-secrets.sh <target> <outdir>        (target: dev or prod; outdir: a new directory)
# Needs BW_CLIENTID, BW_CLIENTSECRET (the bot's API key) and BW_PASSWORD (its master password, which `bw unlock` needs
# even after an API-key login). BW_SERVER defaults to the project's Vaultwarden.
set -euo pipefail

target="$1"
out="$2"
map="$(cd "$(dirname "$0")/.." && pwd)/secrets-map.json"
: "${BW_CLIENTID:?}" "${BW_CLIENTSECRET:?}" "${BW_PASSWORD:?}"

# The CLI's state (its tokens) in a directory of its own, removed at the end.
BITWARDENCLI_APPDATA_DIR="$(mktemp -d)"
export BITWARDENCLI_APPDATA_DIR
trap 'bw logout >/dev/null 2>&1 || true; rm -rf "$BITWARDENCLI_APPDATA_DIR"' EXIT

bw config server "${BW_SERVER:-https://vault.nikolaivorkinn.com}" >/dev/null
bw login --apikey >/dev/null
BW_SESSION="$(bw unlock --passwordenv BW_PASSWORD --raw)"
export BW_SESSION
bw sync >/dev/null

jq -e --arg t "$target" '.[$t]' "$map" >/dev/null || { echo "secrets-map.json has no target $target" >&2; exit 1; }
umask 077
mkdir "$out"

count=0
while IFS=$'\t' read -r file key item; do
  # `bw get` fails when the item is missing or the name matches more than one.
  value="$(bw get password "$item")"
  if [ -z "$value" ]; then echo "Empty value for $item" >&2; exit 1; fi
  # Keep the value out of the job's log, should something print it.
  while IFS= read -r line; do [ -n "$line" ] && echo "::add-mask::$line"; done <<< "$value"
  printf '%s=%s\n' "$key" "$value" >> "$out/$file"
  count=$((count + 1))
done < <(jq -r --arg t "$target" '.[$t] | to_entries[] | .key as $f | .value | to_entries[] | [$f, .key, .value] | @tsv' "$map")

echo "Fetched $count secrets for $target into $(find "$out" -name '*.env' | wc -l | tr -d ' ') files"
