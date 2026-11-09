# Cloudflare DNS

The records for the names each environment's Caddy serves (`APEX_HOST`, `www.`, `AUTH_HOST`, `TAUTH_HOST`,
`ID_HOST`, `LOGS_HOST` and `METRICS_HOST` in `config/<environment>.env`), for dev and production, in the `nikolaivorkinn.com`
zone. Production's are A records to its host. Dev/QA runs on the home server, so its are CNAMEs to `home.nikolaivorkinn.com`
(`cname_targets` in `variables.tf`; home-stack's cloudflare-ddns keeps that name's address current), and `TAUTH_HOST` / `ID_HOST`
get none where `EDGE_OWNED=false`, as they are home-stack's own names. Explicit records are needed because a wildcard
doesn't match below an existing name such as `dev.nikolaivorkinn.com`. They are DNS-only, so nothing sits in front of the servers: the firmware pins Let's Encrypt's roots.

The zone's other records (Resend's `send`, `rsend` and `resend._domainkey`) are not managed here.

## Changing DNS

Edit `host_ips` or `cname_targets` in `variables.tf` (for example, to move production to a new host), or the hostnames in
`config/<environment>.env`, in a pull request. The workflow (`.github/workflows/cloudflare.yml`) comments the plan of
both environments on the PR. Nothing is applied when the PR merges. Each environment is applied by its own deploy, with
its own state (`cloudflare/terraform.tfstate` for dev, `cloudflare/prod/terraform.tfstate` for prod):

- **Release** applies dev's records beside its deploys, after approval in `terraform` if they change;
- **Promote** applies production's beside its deploys, after approval in `terraform-prod` if they change.

A deploy that leaves its DNS as it is asks for no approval. So to cut a name over to another host: merge the change,
release, then promote. If a cutover changes a name or an address, Caddy may ask for its certificate before the record
exists and wait a while to retry, so restart it (`docker restart caddy`) once the apply is done. The *Cloudflare* workflow's *Run workflow* button plans any ref (drift) for one environment, and
with *apply* ticked applies, on the latest release tag only (how a failed apply is retried).

## Setup

State lives in an Oracle Cloud Object Storage bucket, through its S3-compatible API (`backend.tf`), under the keys
`cloudflare/terraform.tfstate` (dev) and `cloudflare/prod/terraform.tfstate` (production). One-time setup in Oracle Cloud:
create a private bucket with versioning on, a customer secret key (Profile > User settings > Customer secret keys) for a
user that may manage objects in it, and note the Object Storage namespace and the bucket's region. Then add the variables
`TF_STATE_BUCKET`, `TF_STATE_REGION` (e.g. `eu-frankfurt-1`) and `TF_STATE_ENDPOINT`
(`https://<namespace>.compat.objectstorage.<region>.oraclecloud.com`) in the repo's Settings > Secrets and variables >
Actions. The workflow sets `AWS_REQUEST_CHECKSUM_CALCULATION` and `AWS_RESPONSE_CHECKSUM_VALIDATION` to `when_required`,
which Oracle's S3 API needs (export both to run Terraform from your own machine). State locking is off, so the workflow's
concurrency group keeps applies to one at a time.

**The credentials are in Vaultwarden, not GitHub** (`secrets-map.json` lists them; the shared action
`nvorkinn/.github/actions/vaultwarden-secrets` reads them into the job's environment), in the collections the deploys'
bots already read, so no bot or collection is special to this:

- `countdown-server/dev`, read by the **dev bot** (its `BW_CLIENTID`, `BW_CLIENTSECRET` and `BW_PASSWORD` are repository
  secrets, as plans run on any pull request): `CLOUDFLARE_API_TOKEN_READ`, a **Zone:DNS:Read** token, which a plan uses as
  `CLOUDFLARE_API_TOKEN`, and the state bucket's key, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`;
- `countdown-server/prod`, read by the **prod bot** (its `BW_*` are secrets of the `terraform` and `terraform-prod`
  environments, so they are only there once the environment's reviewers have approved): `CLOUDFLARE_API_TOKEN_EDIT`, a
  **Zone:DNS:Edit** token, which apply uses as `CLOUDFLARE_API_TOKEN`, and the same two state items (one item each, in both
  collections).

So the write token is readable only with the prod bot's credentials, which only the reviewer-gated `terraform` and
`terraform-prod` environments, and `prod` (limited to `main` and tags), have: a `dev` job, whose bot can't read
`countdown-server/prod`, can't get it. (Both environments' DNS is the one zone, so one Edit token serves both.)

Cloudflare has no OIDC federation for its API, so these are API tokens. Make them **account-owned** (Manage Account >
Account API tokens, which keep working if you leave the organisation), with the single permission **Zone > DNS** (Read or
Edit) and **Zone Resources** limited to the one zone, `nikolaivorkinn.com`; set an expiry if you will rotate them. To rotate
one, edit its item in Vaultwarden.
