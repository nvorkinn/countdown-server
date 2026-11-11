# countdown-server

The backend for **countdown**, the e-paper screens (an ESP32 driving a Waveshare panel) whose frames are drawn by
[countdown](https://github.com/nvorkinn/countdown)'s render pool. It:

- registers screens and pairs each with its owner's login;
- hosts the settings site at **https://auth.countdown.run**;
- holds every account a screen's owner links (Spotify, Apple Music, Glowmarkt, GitHub) and runs the providers that use
  them, so no credential leaves the broker;
- writes each screen's config and subscriptions to Redis for countdown, and serves each screen the frame the render
  pool drew;
- runs the shared infrastructure on the host: the edge (Caddy, Tinyauth and Pocket ID; production only), Redis, Fluent
  Bit, VictoriaLogs and VictoriaMetrics. Production runs on the Oracle Cloud ARM host; dev runs on the home server
  (`nikolai-server`), behind [home-stack](https://github.com/nvorkinn/home-stack)'s edge.

[docs/architecture.md](docs/architecture.md) shows how the pieces fit together and which secret is which;
[docs/database.md](docs/database.md) has the tables.

## Services

Everything runs from `docker-compose.yml` on one host, as the Compose project `countdown-server` (it was `auth-broker`,
the repo's name before it became countdown-server, which is still the name of the host's directory, `/opt/auth-broker/`).
Routes and access rules are Docker labels on the service they belong to, not a separate Caddyfile; see
[The edge](#the-edge).

| Service | What it does |
|---|---|
| `countdown-server` | The Flask app (gunicorn on port 5000 of the `edge` network). State is one SQLite file, `data/broker.db`. |
| `glow-provider` | Fetches each screen's electricity readings from Glowmarkt every 15 minutes into `v1:energy:<device id>`. |
| `spotify-provider` | Fetches what each screen's Spotify and Apple Music are playing (every minute), their top lists (every 30 minutes) and the playing tracks' covers. |
| `developer-provider` | Polls each screen's GitHub and AI plans for the developer panel (every 2 minutes, every 20 seconds while a release runs). |
| `parcel-provider` | Reads each screen's active deliveries from the owner's Parcel account (every 20 minutes by day, not overnight) into `v1:parcels:<device id>`, and whether Parcel took the key into `v1:parcels-status:<device id>`. |
| `caddy`, `tinyauth`, `pocket-id`, `socket-proxy` | The edge, in the compose profile `edge` (prod only): [caddy-docker-proxy](https://github.com/lucaslorentz/caddy-docker-proxy), the login in front of protected routes, the passkey-only identity provider behind it, and the read-only Docker socket proxy the first two use. |
| `redis` | The store shared with countdown. A cache only: nothing on disk. On the compose network as `redis`, and on the host's `127.0.0.1:6379` for countdown. |
| `fluent-bit`, `victoria-logs`, `victoria-metrics` | Logs and metrics from every container, the render pool and the screens. |

The four providers run the broker's own image with another command and the broker's secrets. The glow, developer and parcel
providers open the database read-only; the music provider writes refreshed Spotify tokens back. Each wakes at once when
a config is saved (`v1:sub:changed`).

## The screen API

A screen generates a long random secret on first boot and keeps it. The broker stores only its SHA-256, which
identifies the device. Two calls are all a screen needs:

1. **`POST /api/devices/register`** `{"secret", "width", "height", "format"}`, on every boot and after any 401.
   `format` is the format the screen takes its frame in, `mono-1bpp-packed` or `png`; the renderer draws in it. A new
   secret becomes a device at once (`201 {device_id}`); a known one is a `200`, which changes nothing. Anyone can call
   it, so it's throttled: 5 calls a minute per IP (`429` with `Retry-After`), at most 20 unpaired screens at once
   (`503`), and a screen still unpaired after 24 hours is dropped.
2. **`POST /api/frame`** with `Authorization: Bearer <secret>` and `If-None-Match`, in a loop, sleeping for each
   answer's `Retry-After`:

| Answer | Meaning |
|---|---|
| `200` | The 1-bit frame (800x480, 48,000 bytes) with its `ETag`. |
| `304` | Unchanged. |
| `404` | No frame yet (not paired, or Redis restarted). `Retry-After` is at most 10 s. |
| `401` | Unknown secret (the device was forgotten or unlinked, or sat unpaired for over 24 hours): register again. |

`Retry-After` wakes the screen just after each half-minute (about :03 and :33), as renders land on the minute, or every
10 seconds while a provider's data is bursty. The broker works it out (`broker/services/pace.py`); see
[Pace](docs/architecture.md#pace).

The body of the poll carries the screen's report: `{"metrics": "<InfluxDB line protocol>", "logs": [...]}`. The broker
forwards metrics to VictoriaMetrics (replacing `{device_id}` in them) and logs to VictoriaLogs, in the background; a
failure never fails the poll.

## Pairing and settings

Every browser page needs a Pocket ID login as a member of the group `countdown`. Caddy forward-auths the request to
Tinyauth, which sets the `Remote-*` headers (`Remote-User`, `Remote-Email`, `Remote-Name`, `Remote-Groups`, `Remote-Sub`)
that the broker trusts (so it is reachable only from the `edge` network, and Caddy strips those headers from every
incoming request). The sign-up page (`/pair`) and the device endpoints stay public; `/pair/add` and `/pair/token` need the login.

- **By token (the normal way).** After Wi-Fi setup the screen sends its owner's browser to `/pair?t=<token>` (or
  `/pair#<token>`), where the token is `sha256("pairing:" + sha256hex(secret))`. The page waits, polling
  `POST /pair/token`, until the screen has registered (giving up after 90 seconds), then pairs it and opens its
  settings. Someone else's device is a `409`.
- **By code (a fallback).** `flask devices code <device_id>` issues a 6-character code, valid 10 minutes, which the
  owner enters at `/pair`. The render pool draws it on the screen if the screen has been paired before (it only draws
  named screens); for a new screen, read it from the command's output. A code can also hand a device to a new owner.
  Wrong codes are throttled per IP.

Pairing gives the screen its internal name (its slug, which is its device id), puts it on the render pool's list and
queues its first render. Its owner can give it a friendly name on its settings page.

Each screen's settings are at `/device/<id>` (and `/device` for the most recently paired one): departure stops (TfL and
Entur), the weather place (an Open-Meteo search), a postcode for the notice board, per-screen integration visibility,
language, style, clock format and name, and on an HD screen (1872x1404) how the weather is drawn. Connections and
credentials for music, GitHub, Parcel and Energy live at `/account`, shared across screens; blank replacement
secret fields keep the saved credentials.

Style has a large selected preview and a browsable strip of alternatives; Layout separately chooses the panel
arrangement. Both use native radio choices (arrow keys to select), with scroll buttons when previews do not fit.
The header thumbnail uses the screen's latest real frame, or an empty state before its first render.
`/device/<id>/screen` shows the screen's current frame. "Unlink screen" revokes the screen's
secret; a replacement screen can't yet join an existing device, so it registers as a new one.

Until a weather place is picked, the page suggests the biggest town near the visitor's IP, looked up locally in DB-IP's
[IP to City Lite](https://db-ip.com/db/download/ip-to-city-lite) and [GeoNames](https://www.geonames.org) (both CC BY
4.0), which `scripts/fetch_geodata.py` downloads into the image when it's built.

When a device changes hands (`unpair`, `forget`, or paired by someone else) or an account is removed from its settings,
the providers' data for it is deleted from Redis too (`broker/services/device_keys.py`).

## What's in Redis

SQLite is the source of truth; Redis holds what countdown reads. The broker writes:

- `v1:sub:<source>`: what the providers should fetch (stops, weather places, postcodes, devices), then publishes
  `v1:sub:changed`;
- `v1:config:<device id>`: each paired screen's config (no credentials in it);
- `v1:pairing:<device id>`: the config with the live pairing code, for its 10 minutes;
- `v1:render:screens`: the screens the pool draws (device id to slug), leaving out any not seen for 24 hours;
- render jobs on `v1:render:jobs`, for each screen whose config changed.

The subscriptions are rewritten in full on every config save, when the broker starts and by `flask config publish`.
The configs and the screen list are rewritten then too, and also every minute, writing only what differs. A failed
write is logged and the save still succeeds. The providers
write their data with a TTL, and the broker reads each screen's frame (`v1:frame:<device id>`) and pace hints
(`v1:pace:<source>:<id>`) from the pool and providers.

Every key, its value and TTL is in the store contract, [countdown-api](https://github.com/nvorkinn/countdown-api), used
here as the `vendor/countdown-api` submodule. Its fixtures are what both sides' tests read
(`tests/test_store_contract.py`). To change a stored shape, change countdown-api first, then bump the submodule here
(`git -C vendor/countdown-api checkout <commit>`, `uv lock`). Add fields rather than change them: older readers ignore
fields they don't know.

## Other endpoints

- **`GET /about`** is a public page about the display (no login), with pictures in `broker/static/display/`.
- **`GET /api/status/devices`** lists every device's `device_name`, `created_at`, `paired_at` and `last_seen_at`, for a
  Home Assistant REST sensor. It needs `Authorization: Bearer <STATUS_TOKEN>`, and is a 404 while `STATUS_TOKEN` is
  unset.

```yaml
rest:
  - resource: https://auth.countdown.run/api/status/devices
    headers:
      Authorization: !secret auth_broker_status_token   # "Bearer <STATUS_TOKEN>"
    scan_interval: 300
    sensor:
      - name: Countdown last seen
        value_template: "{{ value_json['<device id>'].last_seen_at }}"
        device_class: timestamp
```

## Development

Python 3.14 and [uv](https://docs.astral.sh/uv/). Clone with the submodule:

```bash
git clone --recurse-submodules https://github.com/nvorkinn/countdown-server.git   # or: git submodule update --init
uv sync && source .venv/bin/activate
cp .env.example .env   # fill in Spotify app credentials, FLASK_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY
export SPOTIFY_REDIRECT_URI=https://auth.countdown.run/auth/spotify/callback
export $(grep -v '^#' .env | xargs)
FLASK_DEBUG=1 python wsgi.py
```

The broker uses Redis at `REDIS_URL` (default `redis://127.0.0.1:6379/0`) with `REDIS_PASSWORD`. There's no login proxy
locally, so send the `Remote-*` headers yourself (e.g. with a header extension). To play a screen without hardware:

```bash
python scripts/simulate_device.py            # registers, and prints how to issue a pairing code
python scripts/simulate_device.py --token    # prints the /pair URL instead
# --secret <random string> to be the same screen across runs; pass a base URL to use the deployed broker
```

**Tests, lint and types** (CI runs all three on every PR, the tests split into 3 shards with coverage to Codecov):

```bash
uv run python -m pytest --cov=broker
ruff check . && ruff format --check .
uv run ty check
npx @biomejs/biome@2.6.3 check --write .   # the pages' JS and CSS; biome.jsonc has the version and scope
```

External APIs are mocked and Redis is fakeredis. The render queue's script tests also run against a real
`redis-server` when one is installed. `node --test tests/browser/*.test.cjs` tests the pages' JavaScript.

**Schema changes.** Edit `broker/models.py`, then generate a migration and read it before committing (autogenerate
misses renames and never backfills data):

```bash
flask --app wsgi db migrate -m "add nickname to devices"
```

The app runs pending migrations on startup, so deploying is all a migration needs.
`test_migrations_match_the_models` fails if a model changes without one.

**Code layout:** `broker/routes/` (Flask blueprints), `broker/services/` (the broker's own logic), `broker/clients/`
(outside APIs), `broker/models.py` and `broker/migrations/` (the schema), `broker/cli.py` (admin commands), and the
providers in `broker/providers/glow.py`, `broker/providers/music.py`, `broker/providers/developer.py` and `broker/providers/parcel.py`, which all
run on the loop in `broker/providers/loop.py`. `tests/` mirrors it.

## Admin commands

Run on the host with `docker exec countdown-server flask --app wsgi <command>`:

| Command | Does |
|---|---|
| `devices list` | List every device. |
| `devices code <device_id>` | Issue a pairing code (drawn on the screen if it has been paired before). |
| `devices owner <device_id> <user id>` | Pair a device with a user. |
| `devices unpair <device_id> [--config]` | Clear its owner and show a new code; `--config` also resets its settings and linked accounts. |
| `devices forget <device_id>` | Delete it; the screen registers again as a new device. |
| `config publish` | Rewrite the configs and subscriptions in Redis. |
| `weather backfill` | Give any config with a typed weather location but no picked place the place Open-Meteo finds first. |

Each change to a device's pairing also updates its config in Redis and queues a render.

## Deploying

Run the **Release** workflow from the Actions tab on `main`, picking `patch`, `minor` or `major` (or push a `v*` tag).
It builds five images, each on a runner of its own architecture (arm64 for the production host, amd64 for dev's home
server; Caddy, which only production runs, for arm64 only) and joins them under one tag, the release and `latest`:

- `ghcr.io/nvorkinn/countdown-server` (the broker and the providers)
- `ghcr.io/nvorkinn/countdown-server-caddy` (caddy-docker-proxy, built with xcaddy)
- `ghcr.io/nvorkinn/countdown-server-fluent-bit` (with `fluent-bit/`)
- `ghcr.io/nvorkinn/countdown-server-victoria-logs`
- `ghcr.io/nvorkinn/countdown-server-victoria-metrics` (with the vmui dashboards)

It also creates the GitHub Release for the tag, with generated notes. A release deploys to **dev** with no approval: starting the release is the
decision. Production is never touched by a release; it gets a tag that has been through dev, by promotion (below). Both
are the same deploy, `deploy-compose.yml`, which fetches the secrets from Vaultwarden (see Secrets) and deploys through the
host's Docker daemon (below): every service, one job. The services are Caddy, the broker and its providers, Fluent Bit,
VictoriaLogs, VictoriaMetrics and Redis, and where `EDGE_OWNED=true` (production) the edge: the Docker socket proxy,
Tinyauth and Pocket ID, at the versions pinned in `docker-compose.yml`. Where `EDGE_OWNED=false` (dev) the edge profile is
off and Caddy is home-stack's. A deploy backs up Pocket ID's
data (see [Backups](#backups)) first, waits until the containers run and are healthy (the broker answering with its database
migrated, Caddy taking connections on 443), and then removes every image no container uses, so the previous release's goes.
Nothing is kept to roll back to: a rollback is a deploy of an older tag, which pulls what it needs. The broker backs up
its own database, when a start has a migration to run (see [Backups](#backups)).

### Promoting to production

Run the **Promote** workflow from the Actions tab on `main` with the release's tag (`v1.2.3`). It builds nothing: it
deploys the images that the Release made, with that tag's `docker-compose.yml` and `config/prod.env`: the same tag that ran
on dev. Its `check` job fails
before anyone is asked if:

- the tag doesn't exist or its commit isn't on `main`;
- the tag wasn't deployed to dev: a deployment to `dev` recorded on the tag's commit has to have succeeded. (A
  Release started with the button runs on `main`, so its deployments carry the tag's commit rather than the tag,
  which is why they are looked up by commit.) The one other way through is a tag that an earlier promotion deployed
  to production, which is a rollback.

Then the `approve-prod` environment's reviewers are asked once for the whole promotion, and `deploy-compose.yml`
deploys to `prod` as it does to dev, in one job: the edge (the socket proxy, Tinyauth and Pocket ID) and every
service, the socket proxy up before Caddy (`depends_on`). Pocket ID's data is backed up first, and every service on a release image is recreated, Caddy's sites
being cut off for a few seconds. To see what a tag would do before promoting it, run **Deploy compose**
by hand with `prod`: it only plans. One promotion runs at a time.

To **roll back production,** promote an older tag that was deployed to dev or promoted before; the same checks and
approval apply (the host keeps the previous images). To roll back dev, release a revert, or run **Deploy compose** by hand
with an older tag. Make compose changes in the repo: each deploy replaces what runs on the host.

**Deploying through the host's Docker daemon.** `deploy-compose.yml` (a release's `deploy (dev)` job, or Actions >
Deploy compose > Run workflow with a release tag) deploys that release to dev without running anything on the host. Run by
hand with `prod` it only plans: it pulls the images and prints what a deploy would do (which containers it would
replace and which it would leave alone), changing no container and taking no backup. It fetches the secrets from Vaultwarden
onto the runner, stages the compose file, settings and `.env` in `/opt/countdown-server` there (a fixed path, so
Compose's config hashes, and with them the services pinned upstream (Redis, Tinyauth, Pocket ID), don't change between
deploys; nothing on the host is read through it), and `scripts/deploy-context.sh` runs `docker compose` against the host
with `DOCKER_HOST=ssh://...`, over one shared SSH connection. One job deploys all the services, so there are no locks. It brings
the edge up before Caddy (`depends_on`), and recreates every service on a release image, as each release builds them all.
Nothing is installed on the host but Docker.

**One-time setup** in the repository's settings:

- **Environments:**
  - `dev` and `prod`, with no reviewers: each only holds its host's secrets. Restrict `prod` to
    `main` and `v*` tags (deployment branches and tags), so no other branch's workflow can reach production's host.
  - `approve-prod` with yourself as a required reviewer: it is the one gate of a promotion. (A job can name only
    one environment, and a matrix of jobs that named a protected one would ask for approval each, which is why the
    approval is not `prod` itself.)
  - `terraform` and `terraform-prod`, each with yourself as a required reviewer (that's what stops the DNS
    records in `infra/cloudflare` applying on their own: a release or a promotion doesn't touch them) and a branch rule: see
    [infra/cloudflare/README.md](infra/cloudflare/README.md).
- **Secrets,** in each of `dev` and `prod` (an environment's secret takes precedence over a repository's of
  the same name, so a repository-level copy is only a fallback, and best deleted once every target has its own):
  `DEPLOY_HOST`, `DEPLOY_USER` (in the `docker` group, owning `/opt/auth-broker/`), `ORACLE_SSH_TOKEN` (a
  private key for that user) and `DEPLOY_SSH_KNOWN_HOSTS` (`ssh-keyscan <host>`), plus the DNS ones in
  [infra/cloudflare/README.md](infra/cloudflare/README.md#setup) and `CODECOV_TOKEN`.
  - **`dev` is the home server,** `nikolai-server` (`DEPLOY_HOST=nikolai-server.tail7c722e.ts.net`, `DEPLOY_USER=ansible`),
    reachable only over Tailscale. Its deploy jobs first join the tailnet as `tag:ci` (the `tailscale/github-action` step,
    skipped where the variable `TAILSCALE_CLIENT_ID` is empty, as in `prod`), so `dev` also needs the variables
    `TAILSCALE_CLIENT_ID` and `TAILSCALE_AUDIENCE`: a Tailscale federated identity (Settings > Trust credentials >
    OpenID Connect, issuer GitHub, subject `repo:nvorkinn/countdown-server:environment:dev`, scope `auth_keys` write with
    `tag:ci`). Neither is secret, and no Tailscale secret is stored anywhere. The tailnet's ACL must let `tag:ci` SSH to the server. The host's `/opt/auth-broker/` comes from [server-config](https://github.com/nvorkinn/server-config).
    Dev has no reverse proxy of its own: its containers join home-stack's Docker network `edge` (`EDGE_OWNED=false`), whose
    Caddy serves them. It does run its own Tinyauth (`tauth.countdown.build`) and Pocket ID (`id.countdown.build`), named
    `countdown-tinyauth` and `countdown-pocket-id` so they don't clash with home-stack's. Dev's names (all in
    `countdown.build`) are CNAMEs to `home.vorkinn.org`; home-stack's Caddy gets their certificates one by one through its
    Cloudflare DNS challenge (its token covers every zone in the account).
- **The host** needs Docker Compose v2 (`sudo apt install docker-compose-v2`), and `curl`.
- **Before the first promotion,** the `countdown-server/prod` collection in Vaultwarden has to hold the production
  secrets (see Secrets below).

### Secrets

Every secret is a login item in [Vaultwarden](https://vault.vorkinn.org), named by its bare variable (`REDIS_PASSWORD`)
with the value as its password, in the collection `countdown-server/dev` or `countdown-server/prod`. The same name in the
two collections is two items, one per environment; an item that several projects share is put in each one's collection. `secrets-map.json` says which item fills which variable of which `.env` file (`broker.env`,
`caddy.env` (the log ingest token), `redis.env` and, where this repo runs the edge, `tinyauth.env` and `pocket-id.env`);
`.env.example` lists the broker's for local development. Settings that aren't secret are in `config/<environment>.env`
and `docker-compose.yml`. Pocket ID's API key for the sign-up flow reaches the broker as `POCKET_ID_API_KEY`, with
`POCKET_ID_COUNTDOWN_GROUP_ID` (both in `broker.env`) and `POCKET_ID_URL` (a setting).

On every deploy, `deploy-compose.yml` runs the shared action `nvorkinn/.github/actions/vaultwarden-secrets`, which logs in to Vaultwarden as the `deploy-bot` user (it
can read only these two collections) and writes one `<name>.env` per file of `secrets-map.json`'s target, on the runner,
where `deploy-context.sh` stages them next to the compose file for Compose to read as the services' `env_file`s. No
secrets file is left on the host (the containers get the values as their environment). A deploy fails, before it changes anything, if Vaultwarden can't be reached or an item is
missing. So Vaultwarden has to be up to deploy (running services don't need it). The `dev` and `prod` environments each need the secrets `BW_CLIENTID`,
`BW_CLIENTSECRET` (the bot's API key) and `BW_PASSWORD` (its master password, which `bw unlock` needs).

The action pins the Bitwarden CLI and always logs in with one fixed device id (the CLI would otherwise invent a new one each
run, and Vaultwarden emails every new device). The countdown repository fetches its secrets the same way, from its own
map and its `countdown/<target>` collections.

To change a secret, edit its item in Vaultwarden and deploy; to add one, create the item, add its name to `secrets-map.json`
under the file it belongs in.

### Settings

What differs between environments and isn't secret is in `config/<environment>.env`: the names the labels route
(`AUTH_HOST` for the settings site, `TAUTH_HOST` for Tinyauth, `ID_HOST` for Pocket ID, `LOGS_HOST`, `METRICS_HOST` and
the bare `APEX_HOST`, which redirects to `AUTH_HOST`), `POCKET_ID_URL`, whether this environment runs its own edge
(`EDGE_OWNED`) and the Spotify redirect URI. `deploy-context.sh` stages the environment's file as `settings.env`
next to `docker-compose.yml`, where the services list it in their `env_file`, and copies it into `.env`, where
Compose fills the names into the labels. One image and one compose file serve every environment. Each name needs an
A record to its environment's edge before Caddy can get a certificate for it (`infra/cloudflare`), and the Spotify redirect URI has to be registered in the
Spotify app. A test fails if the environments' files don't set the same things.

## Operations

### Backups

The `broker_data` volume (the SQLite database, bound to `/opt/auth-broker/data/` on the host) and the `pocket_id_data` volume
(every user and passkey) are the only state worth backing up.

The broker copies its database to `data/broker.db.bak-<revision>` (SQLite's backup API) whenever it starts with a migration
to run, and not otherwise, so a release that changes the schema leaves the database as it was. That is the only copy: the
next migration replaces it, and a start that fails and is restarted keeps the copy from before the first try. It is
on the same disk, so it guards against a bad migration, not a lost one. To go back after one, redeploy the older tag with that
copy restored over `data/broker.db` (an older image can't read a newer schema).

`deploy-context.sh` tars Pocket ID's volume into `/opt/auth-broker/backups/` before each
deploy (the newest 5 are kept); copy that folder off the host, since one disk failure takes both. To restore, stop
`pocket-id`, extract a tarball into the volume (`docker run --rm -v pocket_id_data:/data -v "$PWD/backups:/backup"
alpine tar -xzf /backup/<file> -C /data`) and start it again. Caddy's certificates are in the `caddy_data` volume: never remove it
(`docker compose down -v` would), or Caddy re-requests every certificate and hits Let's Encrypt's rate limits.

### Logs

Fluent Bit tails every container's log and ships it to VictoriaLogs, which keeps a week. The broker posts the
screens' logs there itself (`service=countdown-esp32`, `host=<device id>`). Our own services write JSON lines with
`level` and `_msg`; every record gets `service` (for containers, the container name) and `host`. Browse at
`https://logs.countdown.run/select/vmui` (Pocket ID group `admins` only), e.g. `{service="countdown-server"}`.

### Metrics

VictoriaMetrics keeps a month of pushed metrics: the screens' (via the broker), and the render pool's OTLP
metrics and the host's (via Fluent Bit). Browse at `https://metrics.countdown.run/vmui/` (group `admins` only). Its
Dashboards tab shows `victoria-metrics/dashboards/` (one, **Screens**, per screen and for the render pool); try a query
in vmui before changing one, as CI checks every query parses. The ingest paths on both hosts take
`Authorization: Bearer <LOGS_INGEST_TOKEN>`.

### Redis

Capped at 128 MB, evicting only keys with a TTL. After a restart, the configs and screen list come back within a
minute and the broker's providers refetch on their own. countdown's providers need the subscriptions, which come back
on the next settings save or broker restart; run `flask config publish` to restore them at once.

```bash
docker exec -it redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli'
```

### The edge

Each environment has one Caddy, one Tinyauth and one Pocket ID, and every countdown service joins the Docker network
`edge` that Caddy watches ([caddy-docker-proxy](https://github.com/lucaslorentz/caddy-docker-proxy) builds its
Caddyfile from the `caddy*` labels of the containers on it). Where each runs:

- **Production** runs all three from `docker-compose.yml` under the profile `edge` (`EDGE_OWNED=true`, which `deploy-context.sh`
  turns into `COMPOSE_PROFILES=edge` and a network that compose creates). Caddy reads the labels through a
  [socket proxy](https://github.com/Tecnativa/docker-socket-proxy) that allows read-only endpoints only; nothing else
  mounts the Docker socket.
- **Dev** runs on the home server, on the edge that home-stack runs (`EDGE_OWNED=false`): no profile, and the
  compose file joins its existing external network `edge`. That Caddy, Tinyauth and Pocket ID must be the ones that
  define the `tinyauth` snippet (forward auth to `tinyauth:3000`, `/api/auth/caddy`, copying the `Remote-*` headers) and
  read `tinyauth.apps.*` labels.

Tinyauth defines the snippet once, in its own labels (`caddy_0: (tinyauth)`), and a protected route adds
`import tinyauth`. Its only provider is Pocket ID (auto-redirect, no local users), and every protected app's
`tinyauth.apps.<app>.oauth.groups` label names the Pocket ID group allowed in: `countdown` for the settings site,
`admins` for the Victoria UIs. VictoriaLogs and VictoriaMetrics ingest (`/insert/*`, the metrics write paths) stays on
its bearer-token route with no login. Tinyauth 5.1 refused every OAuth user of an app restricted only by groups
([tinyauth#1016](https://github.com/steveiliop56/tinyauth/issues/1016)); the pinned 5.2.0 may have fixed it, but the
OAuth whitelist `/.*/` is still set globally and on each app, so the group is the only rule: **every protected app must
have a group label**, or any Pocket ID user can open it (a test checks).

**Hostnames are permanent.** Passkeys are bound to Pocket ID's hostname (`id.<domain>`, `ID_HOST`), so changing it
locks everyone out. Tinyauth is at `tauth.<domain>` (`TAUTH_HOST`).

**First start of an environment's Pocket ID** (it starts empty):

1. Open `https://id.<domain>/setup` and create the admin account with a passkey.
2. Create the groups `admins` and `countdown`; add yourself to both.
3. Create an OIDC client for Tinyauth with the callback URL `https://tauth.<domain>/api/oauth/callback/pocketid`; its id
   and secret go in Vaultwarden, as the `tinyauth.env` items of `secrets-map.json`. Restart Tinyauth.
4. Create an API key (Settings > Admin > API Keys) and note the `countdown` group's id: they go in
   Vaultwarden, as the `broker.env` items `POCKET_ID_API_KEY` and `POCKET_ID_COUNTDOWN_GROUP_ID`.

`ENCRYPTION_KEY` (`pocket-id.env`) is generated once and never changed.

### Request tracing

Setting `LOG_REQUEST_HEADERS: "true"` in `docker-compose.yml` makes the broker log every
request's headers (never `Authorization` or cookies), for debugging sign-in redirects. Off by default.

## PR status check

One check, **Global PR Status**, is the only one branch protection needs to require. Other workflows only run when
their paths change, so requiring them directly would block every PR that doesn't touch them.

- [`create_global_pr_status.yml`](.github/workflows/create_global_pr_status.yml) opens the check, in progress, on the
  PR's head commit.
- [`all-checks.yml`](.github/workflows/all-checks.yml) runs whenever one of the workflows it lists finishes (a
  `workflow_run` trigger, so nothing polls). It finds the check by name and looks at every workflow run and check on the
  commit. While any is still running it does nothing, and the last one to finish runs it again. Once all have passed it
  approves the PR (`approve: true`) and then completes the check as `success`; if any failed, it completes it as
  `failure`. Approval skips drafts, forks, Dependabot and `github-actions[bot]`, and needs **Allow GitHub Actions to
  create and approve pull requests** enabled in the repository's settings.

Both call reusable workflows in [`nvorkinn/.github`](https://github.com/nvorkinn/.github), pinned to a commit; the
triggers and the list of workflows stay here. When you add a workflow that runs on pull requests, add its `name` to
`workflows:` in `all-checks.yml` (a test fails until you do), so its completion re-runs the gate. Fork and Dependabot
PRs get a read-only token that can't create the check, so they have no Global PR Status and aren't approved.
