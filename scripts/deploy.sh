#!/usr/bin/env bash
# Ships in each release's deploy bundle, next to that release's
# docker-compose.yml; fetch-release.sh unpacks it into
# /opt/auth-broker/releases/<tag>/ and runs it. It installs the bundled
# docker-compose.yml, pins the service to the tag in .env, then pulls and
# recreates just that service.
# Usage: deploy.sh <countdown-server|caddy|fluent-bit|victoria-logs|victoria-metrics|redis|edge> <tag>
#
# The bundle's settings.env (the environment's non-secret settings) is installed next to the compose file too.
#
# Every secrets/<name>.env in the bundle is decrypted on every deploy, with the
# host's age key, into <name>.env next to the compose file (compose won't load
# the file if any service's env_file is missing). Nobody edits those files.
#
# edge (the Docker socket proxy, Tinyauth and Pocket ID) and redis run upstream's images, pinned by hand in
# docker-compose.yml, so they aren't pinned to the release tag. They're brought up rather than force-recreated, so a
# release that doesn't change them leaves them running.
#
# Whether this host runs the edge itself (Caddy, Tinyauth, Pocket ID: the `edge` compose profile) is EDGE_OWNED in the
# bundle's settings.env. Where it is false (dev/QA, on home-stack's edge), the caddy and edge deploys do nothing, and the
# services join home-stack's external Docker network `edge`.
set -euo pipefail

service="$1"
tag="$2"
bundle="$(cd "$(dirname "$0")" && pwd)"
cd "${DEPLOY_DIR:-/opt/auth-broker}"

# sops itself, downloaded once per version into bin/ and checked against
# upstream's published checksums.
SOPS_VERSION=v3.13.3
case "$(uname -m)" in
  aarch64) sops_arch=arm64 sops_sha256=53b0abacd38ef1b12a66d6c100956691b9cefce018d91f81e73ddf7438b94d77 ;;
  x86_64) sops_arch=amd64 sops_sha256=e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b ;;
  *) echo "no sops build for $(uname -m)" >&2; exit 1 ;;
esac

# The settings (config/<environment>.env), read here to tell whether this host owns the edge.
edge_owned="$(sed -n 's/^EDGE_OWNED=//p' "$bundle/settings.env")"
case "$service" in
  caddy | edge)
    if [ "$edge_owned" != true ]; then
      echo "$service: this environment's edge is not this repo's (EDGE_OWNED=$edge_owned), so nothing to deploy"
      exit 0
    fi
    ;;
esac

services=("$service")
var=
case "$service" in
  # This repo's images, each pinned by <SERVICE>_VERSION in .env, e.g.
  # fluent-bit -> FLUENT_BIT_VERSION.
  countdown-server | caddy | fluent-bit | victoria-logs | victoria-metrics)
    var="${service^^}"
    var="${var//-/_}_VERSION"
    # The providers run the broker's image (and so its COUNTDOWN_SERVER_VERSION), so they're recreated with it.
    if [ "$service" = countdown-server ]; then
      services=(countdown-server glow-provider spotify-provider developer-provider parcel-provider)
    fi
    ;;
  edge) services=(socket-proxy tinyauth pocket-id) ;;
  redis) ;;
  *) echo "unknown service: $service" >&2; exit 1 ;;
esac
export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-$PWD/age.key}"
if [ ! -r "$SOPS_AGE_KEY_FILE" ]; then
  echo "No age key at $SOPS_AGE_KEY_FILE to decrypt the secrets; see Secrets in the README" >&2
  exit 1
fi
sops="$PWD/bin/sops-$SOPS_VERSION"
if [ ! -x "$sops" ]; then
  mkdir -p bin
  # Unique temp name, then mv, so two deploy jobs downloading at once can't
  # leave a half-written binary.
  tmp="$(mktemp bin/.sops-XXXXXX)"
  curl -fsSL --retry 3 -o "$tmp" \
    "https://github.com/getsops/sops/releases/download/$SOPS_VERSION/sops-$SOPS_VERSION.linux.$sops_arch"
  echo "$sops_sha256  $tmp" | sha256sum -c --quiet -
  chmod 755 "$tmp"
  mv "$tmp" "$sops"
fi
for encrypted in "$bundle"/secrets/*.env; do
  name="$(basename "$encrypted")"
  # What the deploy workflow fetched from Vaultwarden (scripts/fetch-secrets.sh), if this environment has it set up,
  # is used as it is; otherwise the bundle's sops file is decrypted.
  if [ -r "vault-secrets/$name" ]; then
    tmp="$(mktemp "$name.XXXXXX")"
    cp "vault-secrets/$name" "$tmp"
    mv "$tmp" "$name"
    echo "Installed $name from Vaultwarden"
    continue
  fi
  # mktemp's file is readable by the deploy user only, and unique per job, as
  # the deploy jobs can run at once; mv replaces the old one in one step.
  tmp="$(mktemp "$name.XXXXXX")"
  "$sops" decrypt --output-type dotenv "$encrypted" > "$tmp"
  mv "$tmp" "$name"
  echo "Decrypted $name"
done

DB_BACKUPS_KEPT=5
# The broker's running container: auth-broker is its name from before the repo was renamed, still running on the
# first deploy under the new one.
broker=
if [ "$service" = countdown-server ]; then
  for name in countdown-server auth-broker; do
    if docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true; then broker="$name"; break; fi
  done
fi
if [ -n "$broker" ]; then
  # SQLite's backup API, so the copy is consistent even mid-write.
  docker exec "$broker" python -c "
import sqlite3
src = sqlite3.connect('/app/data/broker.db')
src.backup(sqlite3.connect('/app/data/broker.db.bak-$tag'))
# Keep the newest $DB_BACKUPS_KEPT copies; deleted in here as the container
# owns them.
from pathlib import Path
backups = sorted(Path('/app/data').glob('broker.db.bak-*'), key=lambda p: p.stat().st_mtime)
for old in backups[:-$DB_BACKUPS_KEPT]:
    old.unlink()
    print(f'Removed old backup data/{old.name}')
"
  echo "Backed up the database to data/broker.db.bak-$tag"
fi

# The deploy jobs can run at once, and each edits .env and the compose file.
(
  flock 9
  # Copy then mv, so compose never reads a half-written file.
  cp "$bundle/docker-compose.yml" docker-compose.yml.tmp
  mv docker-compose.yml.tmp docker-compose.yml
  # This environment's settings (config/<environment>.env): the hostnames and the like, which aren't secret.
  cp "$bundle/settings.env" settings.env.tmp
  mv settings.env.tmp settings.env
  # Keep only the version pins, and add what Compose fills into docker-compose.yml's labels and networks: the settings
  # (hostnames and the like, not secret) and which edge profile and network this environment has. No secret goes in
  # here: Compose would copy it into labels, where `docker inspect` and Caddy's logs show it. The secrets are in the
  # *.env files (the log ingest token reaches Caddy's routes as the runtime placeholder {env.LOGS_INGEST_TOKEN}).
  # AUTH_BROKER_VERSION is the broker's pin from before the rename (now COUNTDOWN_SERVER_VERSION).
  (
    umask 077
    { grep -E '^[A-Z0-9_]+_VERSION=' .env | grep -v '^AUTH_BROKER_VERSION=' || true; } > .env.tmp
    grep -E '^[A-Z][A-Z0-9_]*=' settings.env >> .env.tmp
    if [ "$edge_owned" = true ]; then
      printf 'COMPOSE_PROFILES=edge\nEDGE_NETWORK_EXTERNAL=false\n' >> .env.tmp
    else
      printf 'COMPOSE_PROFILES=\nEDGE_NETWORK_EXTERNAL=true\n' >> .env.tmp
    fi
  )
  mv .env.tmp .env
  if [ -n "$var" ]; then
    if grep -q "^$var=" .env; then
      sed -i "s/^$var=.*/$var=$tag/" .env
    else
      echo "$var=$tag" >> .env
    fi
  fi
) 9>.env.lock
echo "Installed $tag's docker-compose.yml${var:+ and pinned $var=$tag in .env}"

# docker compose commands that pull (pull itself, and up, which pulls again what it thinks is missing, e.g. the four
# services of the broker image at once) now and then fail with containerd's "lease does not exist: not found". Another
# try works, so this runs the command up to three times, but only for that error: anything else (a container that
# never gets healthy) fails at once, not after three timeouts.
retry_lost_lease() {
  local attempt log
  log="$(mktemp)"
  for attempt in 1 2 3; do
    if "$@" >"$log" 2>&1; then
      cat "$log"
      rm -f "$log"
      return 0
    fi
    cat "$log"
    if ! grep -q 'lease' "$log" || [ "$attempt" -eq 3 ]; then
      rm -f "$log"
      return 1
    fi
    echo "Lost a containerd lease (attempt $attempt of 3); trying again"
    sleep 5
  done
}

if [ "$service" = edge ]; then
  # Pocket ID's data volume holds every user and passkey, so it is copied before each edge deploy, whether or not Pocket
  # ID changes: a tarball in backups/ (the newest $DB_BACKUPS_KEPT are kept). The volume doesn't exist on the very first
  # deploy. A copy taken mid-write could be unreadable; the earlier ones are kept. Copy backups/ off the host as well
  # (see "Backups" in the README).
  if docker volume inspect pocket_id_data >/dev/null 2>&1; then
    mkdir -p backups
    backup="pocket-id-$tag-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    docker run --rm -v pocket_id_data:/data:ro -v "$PWD/backups:/backup" docker.io/library/alpine:3.22 \
      tar -czf "/backup/$backup" -C /data .
    echo "Backed up Pocket ID's data to backups/$backup"
    # shellcheck disable=SC2012
    ls -1t backups/pocket-id-*.tar.gz | tail -n +"$((DB_BACKUPS_KEPT + 1))" | xargs -r rm -f
  fi
  # The login this replaced, gone from hosts that ran it. Its volumes (authentik_db, authentik_media) are left for the
  # owner to remove once nothing needs them.
  docker rm --force authentik-server authentik-worker authentik-postgresql >/dev/null 2>&1 || true
fi

# Another deploy job's cleanup (below) can delete layers this one is still pulling, so pulls hold image.lock too, and
# so does the cleanup. Pulls take turns as well.
(
  flock 9
  retry_lost_lease docker compose pull "${services[@]}"
) 9>image.lock
if [ "$service" = countdown-server ]; then
  # The broker's container from before the rename, which compose no longer knows: it holds 127.0.0.1:5000, so the
  # new one couldn't start beside it.
  docker rm --force auth-broker >/dev/null 2>&1 && echo "Removed the old auth-broker container" || true
fi
# Whether every one of the services runs the image just pulled with the settings it would get now, the tag aside: an
# infra image that didn't change since the last release is that release's image under the new tag (release.yml), and
# recreating Caddy for it would cut every site off for a few seconds. Compose's config hash covers the image name, so the
# hash is worked out with the version pin set back to the running container's tag, and compared with the one compose
# labelled the container with. Changed secrets or settings change the hash, so those still recreate.
unchanged() {
  local s cid running_ref new_ref hash
  for s in "${services[@]}"; do
    cid="$(docker compose ps -q "$s")"
    [ -n "$cid" ] || return 1
    running_ref="$(docker inspect -f '{{.Config.Image}}' "$cid")"
    new_ref="$(docker compose config --images "$s")"
    [ "$(docker inspect -f '{{.Image}}' "$cid")" = "$(docker image inspect -f '{{.Id}}' "$new_ref")" ] || return 1
    hash="$(env "$var=${running_ref##*:}" docker compose config --hash "$s" | awk '{print $2}')"
    [ "$hash" = "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$cid")" ] || return 1
  done
}

if [ -n "$var" ] && unchanged; then
  echo "${services[*]} already run$([ "${#services[@]}" -gt 1 ] || echo s) this image and settings, so left running"
elif [ -n "$var" ]; then
  # --wait: the job ends once the containers are running, and healthy where they have a healthcheck (Caddy listening
  # on 443), so whatever runs after the deploy finds them serving, and a container that won't start fails the job.
  retry_lost_lease docker compose up -d --no-deps --force-recreate --wait --wait-timeout 300 "${services[@]}"
else
  # Recreates only what changed. Jobs for these can run at once, so
  # they take turns.
  (
    flock 9
    retry_lost_lease docker compose up -d --wait --wait-timeout 600 "${services[@]}"
  ) 9>up.lock
fi
docker compose ps "${services[@]}"

# Every release pulls new tags, so drop the older ones of what was just deployed: keep the running tag and the
# newest other one (to roll back to), remove the rest. `docker image rm` refuses an image a container still uses,
# and only these repositories are touched, so a deploy job running at once never loses an image it just pulled.
IMAGES_KEPT=2
(
  flock 9
  for image in $(docker compose config --images "${services[@]}" | sort -u); do
    repo="${image%:*}"
    docker images "$repo" --format '{{.Repository}}:{{.Tag}}' | { grep -vxF "$image" || true; } |
      tail -n +"$IMAGES_KEPT" | while read -r old; do
        docker image rm "$old" >/dev/null 2>&1 && echo "Removed old image $old" || true
      done
    # And every tag of this repo's image from before the rename (ghcr.io/nvorkinn/auth-broker*), now that nothing
    # runs it.
    old_repo="${repo/\/countdown-server/\/auth-broker}"
    if [ "$old_repo" != "$repo" ]; then
      docker images "$old_repo" --format '{{.Repository}}:{{.Tag}}' | while read -r old; do
        docker image rm "$old" >/dev/null 2>&1 && echo "Removed old image $old" || true
      done
    fi
  done
  # Layers left untagged by the above or by re-pulled tags.
  docker image prune -f >/dev/null
) 9>image.lock

if [ "$service" = countdown-server ]; then
  # A check that the database came through.
  docker exec countdown-server flask --app wsgi devices list
fi
