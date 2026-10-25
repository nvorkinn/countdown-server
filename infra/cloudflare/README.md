# Cloudflare DNS

The A records for the names each environment's Caddy serves (`APEX_HOST`, `www.`, `AUTH_HOST`, `SSO_HOST`,
`LOGS_HOST` and `METRICS_HOST` in `config/<environment>.env`), for dev/QA and production, in the `nikolaivorkinn.com`
zone. They are DNS-only, so nothing sits in front of the servers: the firmware pins Let's Encrypt's roots.

The zone's other records (Resend's `send`, `rsend` and `resend._domainkey`) are not managed here.

## Changing DNS

Edit `host_ips` in `variables.tf` (for example, to move production to a new host), or the hostnames in
`config/<environment>.env`, in a pull request. The workflow (`.github/workflows/cloudflare.yml`) comments the plan on the
PR. Nothing is applied when the PR merges: the next **Release** plans DNS before it deploys anything and, if it changes,
waits for approval in `terraform-production` and applies it. A release that leaves DNS as it is asks for no approval.
So to cut a name over to another host: merge the change, release, then promote. The *Cloudflare* workflow's *Run workflow*
button plans any ref (drift), and with *apply* ticked applies, on the latest release tag only (how a failed apply is
retried).

## Setup

The state is in the same Oracle bucket as `terraform/` (the `TF_STATE_*` variables and the `AWS_*` secrets are shared), under
the key `cloudflare/terraform.tfstate`. It also needs a Cloudflare API token, `CLOUDFLARE_API_TOKEN`:

- a repository secret, a **Zone:DNS:Read** token for `nikolaivorkinn.com`, which plans use;
- a secret of the `terraform-production` environment, a **Zone:DNS:Edit** token for the same zone, which apply uses.

(Create them under My Profile > API Tokens > Create Token > Edit zone DNS, limited to the one zone.)

## The first apply

The 12 existing records are imported (`imports.tf`), so the first plan shows them updated in place (a comment is added) and
nothing created or destroyed. Once an apply has put them in the state, `imports.tf` can be deleted.
