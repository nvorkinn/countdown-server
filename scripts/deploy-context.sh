#!/usr/bin/env bash
# Deploys a release to one host from the runner, through the Docker daemon on the host (DOCKER_HOST=ssh://...), instead
# of running deploy.sh on the host: the compose file, the settings and the secrets are read here, and only containers,
# images and the data directory are on the host. Used by deploy-compose.yml.
# Usage: deploy-context.sh <target> <tag> <secrets dir>     (target: dev or prod; secrets dir: fetch-secrets.sh's output)
#
# The project directory is /opt/auth-broker here as well as on the host, so that docker-compose.yml's relative paths
# (./data) mean the same on both ends, and the project is called auth-broker as the host's containers already are.
#
# Left to do before production uses this (deploy.sh does them): the edge's Pocket ID backup, recreating Caddy only when
# its image or settings changed (the infra images are re-tagged each release), and the one-time removals of the
# pre-rename broker and its images.
set -euo pipefail

target="$1"
tag="$2"
secrets="$3"
: "${DOCKER_HOST:?}"
# The release's compose file and settings: this checkout's unless RELEASE_DIR is another one (the tag being deployed).
release="${RELEASE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
dir="${PROJECT_DIR:-/opt/auth-broker}"
DB_BACKUPS_KEPT=5
IMAGES_KEPT=2

[ -w "$dir" ] || { echo "$dir must exist here and be writable: the project directory mirrors the host's" >&2; exit 1; }
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

if [ "$(docker inspect -f '{{.State.Running}}' countdown-server 2>/dev/null || true)" = true ]; then
  # SQLite's backup API, so the copy is consistent even mid-write.
  docker exec countdown-server python -c "
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

retry_lost_lease docker compose pull
# --wait: the step ends once the containers are running, and healthy where they have a healthcheck, so a container that
# won't start fails the deploy. Recreates only what changed.
retry_lost_lease docker compose up -d --wait --wait-timeout 600
docker compose ps

# Every release pulls new tags, so drop the older ones of what was just deployed: keep the running tag and the newest
# other one (to roll back to). `docker image rm` refuses an image a container still uses.
for image in $(docker compose config --images | sort -u); do
  repo_name="${image%:*}"
  docker images "$repo_name" --format '{{.Repository}}:{{.Tag}}' | { grep -vxF "$image" || true; } |
    tail -n +"$IMAGES_KEPT" | while read -r old; do
      docker image rm "$old" >/dev/null 2>&1 && echo "Removed old image $old" || true
    done
done
docker image prune -f >/dev/null

# A check that the database came through.
docker exec countdown-server flask --app wsgi devices list
