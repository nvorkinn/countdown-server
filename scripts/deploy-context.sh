#!/usr/bin/env bash
# Deploys a release to one host from the runner, through the Docker daemon on the host (DOCKER_HOST=ssh://...), without
# running anything on the host: the compose file, the settings and the secrets are read here, and only containers,
# images and the data directory are on the host. Used by deploy-compose.yml (release.yml and promote.yml).
# Usage: deploy-context.sh <target> <tag> <secrets dir>     (target: dev or prod; secrets dir: the vaultwarden-secrets action's output)
#
# The project is called auth-broker, as the host's containers already are. Its directory here is /opt/auth-broker too,
# as it always was: nothing in docker-compose.yml reads the host's filesystem through it any more (the database is the
# broker_data volume), but Compose labels each container with a hash of its configuration, which includes the paths of
# the env files, so a different directory would recreate every container once.
#
# DRY_RUN=true pulls the images and prints what `docker compose up` would do, but changes no container, backs nothing up
# and removes no image: to see what a deploy to a host would do before it does it.
#
# The database's pre-migration backup is the app's own (broker/db.py), and the broker's healthcheck is what `--wait`
# waits on, so no command runs inside a container here.
#
# The first deploy to a host that another Compose version set up may recreate its containers once (the config hashes
# Compose labels them with differ between versions); after that only what changed is recreated.
set -euo pipefail

target="$1"
tag="$2"
secrets="$3"
: "${DOCKER_HOST:?}"
# The release's compose file and settings: this checkout's unless RELEASE_DIR is another one (the tag being deployed).
release="${RELEASE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
dir="${PROJECT_DIR:-/opt/auth-broker}"
BACKUPS_KEPT=5
# Where Pocket ID's backups go, on the host (the daemon reads this path there, not on the runner).
host_backups=/opt/auth-broker/backups
dry_run="${DRY_RUN:-false}"

[ -w "$dir" ] || { echo "$dir must exist here and be writable: the project directory is kept at the host's path" >&2; exit 1; }
cd "$dir"
export COMPOSE_PROJECT_NAME=auth-broker

# The project: the compose file, this environment's settings and secrets, and the .env Compose fills the compose file
# from. No secret goes in .env: Compose would copy it into labels, where `docker inspect` shows it. The secrets are in
# the *.env files, which the services list as env_file and Compose reads here and gives the containers.
cp "$release/docker-compose.yml" docker-compose.yml
cp "$release/config/$target.env" settings.env
(umask 077 && cp "$secrets"/*.env .)
edge_owned="$(sed -n 's/^EDGE_OWNED=//p' settings.env)"
(
  umask 077
  # Every version pin the compose file has, at the release's tag.
  # shellcheck disable=SC2016  # the pattern is a literal ${
  grep -o '\${[A-Z_]*_VERSION' docker-compose.yml | cut -c3- | sort -u | sed "s/\$/=$tag/" > .env
  grep -E '^[A-Z][A-Z0-9_]*=' settings.env >> .env
  if [ "$edge_owned" = true ]; then
    printf 'COMPOSE_PROFILES=edge\nEDGE_NETWORK_EXTERNAL=false\n' >> .env
  else
    printf 'COMPOSE_PROFILES=\nEDGE_NETWORK_EXTERNAL=true\n' >> .env
  fi
)
echo "Staged $tag for $target ($(docker compose config --services | wc -l | tr -d ' ') services, DOCKER_HOST=${DOCKER_HOST%%@*}@...)"

# docker compose commands that pull (pull itself, and up, which pulls again what it thinks is missing) now and then fail
# with containerd's "lease does not exist: not found". Another try works, so this runs the command up to three times, but
# only for that error: anything else (a container that never gets healthy) fails at once, not after three timeouts.
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

if [ "$dry_run" != true ] && [ "$edge_owned" = true ] && docker volume inspect pocket_id_data >/dev/null 2>&1; then
  # Pocket ID's data volume holds every user and passkey, so it is copied before each deploy, whether or not Pocket ID
  # changes: a tarball in backups/ on the host (the newest $BACKUPS_KEPT are kept). A copy taken mid-write could be
  # unreadable; the earlier ones are kept. Copy backups/ off the host as well (see "Backups" in the README).
  backup="pocket-id-$tag-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  docker run --rm -v pocket_id_data:/data:ro -v "$host_backups:/backup" docker.io/library/alpine:3.22 \
    tar -czf "/backup/$backup" -C /data .
  echo "Backed up Pocket ID's data to backups/$backup"
  docker run --rm -v "$host_backups:/backup" docker.io/library/alpine:3.22 \
    sh -c "ls -1t /backup/pocket-id-*.tar.gz | tail -n +$((BACKUPS_KEPT + 1)) | xargs -r rm -f"
fi

retry_lost_lease docker compose pull

# Whether a service runs the image just pulled with the settings it would get now, the tag aside: an infra image that
# didn't change since the last release is that release's image under the new tag (release.yml), and recreating Caddy
# for it would cut every site off for a few seconds. Compose's config hash covers the image name, so the hash is worked
# out with the version pin set back to the running container's tag, and compared with the one Compose labelled the
# container with. Changed secrets or settings change the hash, so those still recreate.
unchanged() {
  local var="$1" service="$2" cid running_ref new_ref hash
  cid="$(docker compose ps -q "$service")"
  [ -n "$cid" ] || return 1
  running_ref="$(docker inspect -f '{{.Config.Image}}' "$cid")"
  new_ref="$(docker compose config --images "$service")"
  [ "$(docker inspect -f '{{.Image}}' "$cid")" = "$(docker image inspect -f '{{.Id}}' "$new_ref")" ] || return 1
  hash="$(env "$var=${running_ref##*:}" docker compose config --hash "$service" | awk '{print $2}')"
  [ "$hash" = "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$cid")" ]
}

# The release's images are pinned by a variable each (ghcr.io/nvorkinn/<repo>:${<NAME>_VERSION}); one whose services
# all run what was just pulled keeps its old tag in .env, so `up` leaves them alone.
config_json="$(docker compose config --format json)"
# shellcheck disable=SC2016  # the pattern is a literal ${
while read -r image_repo var; do
  services="$(jq -r --arg r "$image_repo:" '.services | to_entries[] | select(.value.image | startswith($r)) | .key' <<< "$config_json")"
  [ -n "$services" ] || continue
  all_unchanged=true
  for service in $services; do
    unchanged "$var" "$service" || { all_unchanged=false; break; }
  done
  if [ "$all_unchanged" = true ]; then
    first="$(echo "$services" | head -n1)"
    running_tag="$(docker inspect -f '{{.Config.Image}}' "$(docker compose ps -q "$first")")"
    running_tag="${running_tag##*:}"
    sed -i "s/^$var=.*/$var=$running_tag/" .env
    echo "$(echo "$services" | tr '\n' ' ')already run this image and settings, so left at $running_tag"
  fi
done < <(grep -oE 'ghcr\.io/[a-z0-9/_.-]+:\$\{[A-Z_]+_VERSION' docker-compose.yml | sed -E 's/:\$\{/ /' | sort -u)

if [ "$dry_run" = true ]; then
  echo "Dry run: this is what a deploy would do (containers: Recreate = replaced, Running = left alone)"
  docker compose --dry-run up -d --no-build
  exit 0
fi

# --wait: the step ends once the containers are running, and healthy where they have a healthcheck (Caddy listening on
# 443), so whatever runs after finds them serving, and a container that won't start fails the deploy. Recreates only
# what changed; the services start in the order of their depends_on (Caddy after the socket proxy).
retry_lost_lease docker compose up -d --wait --wait-timeout 600
docker compose ps

# Every release pulls new tags, so drop every image no container uses: the previous release's, and anything else left
# behind. Nothing is kept to roll back to, as a rollback is a deploy of an older tag, which pulls what it needs.
docker image prune -af >/dev/null
