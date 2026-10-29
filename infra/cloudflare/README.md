# Cloudflare DNS

The A records for the names each environment's Caddy serves (`APEX_HOST`, `www.`, `AUTH_HOST`, `SSO_HOST`,
`LOGS_HOST` and `METRICS_HOST` in `config/<environment>.env`), for dev/QA and production, in the `nikolaivorkinn.com`
zone. They are DNS-only, so nothing sits in front of the servers: the firmware pins Let's Encrypt's roots.

On dev/QA, `MAXMIX_HOST` (THE MAX MIX, hosted on Vercel) is a DNS-only CNAME to `cname.vercel-dns.com` instead. It was an
A record to the host, so the apply that introduced the CNAME replaces that record (delete, then create).

The zone's other records (Resend's `send`, `rsend` and `resend._domainkey`) are not managed here.

## Changing DNS

Edit `host_ips` in `variables.tf` (for example, to move production to a new host), or the hostnames in
`config/<environment>.env`, in a pull request. The workflow (`.github/workflows/cloudflare.yml`) comments the plan of
both environments on the PR. Nothing is applied when the PR merges. Each environment is applied by its own deploy, with
its own state (`cloudflare/terraform.tfstate` for dev, `cloudflare/prod/terraform.tfstate` for prod):

- **Release** applies dev/QA's records beside its deploys, after approval in `terraform` if they change;
- **Promote** applies production's beside its deploys, after approval in `terraform-production` if they change.

A deploy that leaves its DNS as it is asks for no approval. So to cut a name over to another host: merge the change,
release, then promote. If a cutover changes a name or an address, Caddy may ask for its certificate before the record
exists and wait a while to retry, so restart it (`docker restart caddy`) once the apply is done. The *Cloudflare* workflow's *Run workflow* button plans any ref (drift) for one environment, and
with *apply* ticked applies, on the latest release tag only (how a failed apply is retried).

## Setup

The state is in the same Oracle bucket as `terraform/` (the `TF_STATE_*` variables and the `AWS_*` secrets are shared), under its own keys,
`cloudflare/terraform.tfstate` and `cloudflare/prod/terraform.tfstate`. It also needs a Cloudflare API token, `CLOUDFLARE_API_TOKEN`:

- a repository secret, a **Zone:DNS:Read** token for `nikolaivorkinn.com`, which plans use;
- a secret of each of the `terraform` (dev/QA) and `terraform-production` environments, a **Zone:DNS:Edit** token for
  the same zone, which apply uses.

(Create them under My Profile > API Tokens > Create Token > Edit zone DNS, limited to the one zone.)
