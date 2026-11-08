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
# The CLI makes up a random device id (global_applicationId_appId) in a fresh state directory, so every run would be a
# new device to Vaultwarden, which emails "new device logged in" each time. A fixed one makes it the same device.
cp "$(dirname "$0")/bitwarden-cli/data.json" "$BITWARDENCLI_APPDATA_DIR/data.json"

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
  if ! value="$(bw get password "$item" 2>&1)"; then
    # Names only, never values: what this user can see, to tell a missing item from a missing membership or collection.
    {
      echo "Can't read $item: $value"
      echo "This user sees $(bw list organizations | jq length) organizations, collections [$(bw list collections | jq -r '[.[].name] | join(", ")')]"
      echo "and $(bw list items | jq length) items; those matching ${item##*/}: [$(bw list items --search "${item##*/}" | jq -r '[.[].name] | join(", ")')]"
    } >&2
    exit 1
  fi
  if [ -z "$value" ]; then echo "Empty value for $item" >&2; exit 1; fi
  # Keep the value out of the job's log, should something print it.
  while IFS= read -r line; do [ -n "$line" ] && echo "::add-mask::$line"; done <<< "$value"
  printf '%s=%s\n' "$key" "$value" >> "$out/$file"
  count=$((count + 1))
done < <(jq -r --arg t "$target" '.[$t] | to_entries[] | .key as $f | .value | to_entries[] | [$f, .key, .value] | @tsv' "$map")

echo "Fetched $count secrets for $target into $(find "$out" -name '*.env' | wc -l | tr -d ' ') files"
