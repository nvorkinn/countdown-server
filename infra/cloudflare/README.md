# Cloudflare DNS

The A records for the names each environment's Caddy serves (`APEX_HOST`, `www.`, `AUTH_HOST`, `TAUTH_HOST`,
`ID_HOST`, `LOGS_HOST` and `METRICS_HOST` in `config/<environment>.env`), for dev/QA and production, in the `nikolaivorkinn.com`
zone. They are DNS-only, so nothing sits in front of the servers: the firmware pins Let's Encrypt's roots.

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

State lives in an Oracle Cloud Object Storage bucket, through its S3-compatible API (`backend.tf`), under the keys
`cloudflare/terraform.tfstate` (dev/QA) and `cloudflare/prod/terraform.tfstate` (production). One-time setup in Oracle Cloud:
create a private bucket with versioning on, a customer secret key (Profile > User settings > Customer secret keys) for a
user that may manage objects in it, and note the Object Storage namespace and the bucket's region. Then add the variables
`TF_STATE_BUCKET`, `TF_STATE_REGION` (e.g. `eu-frankfurt-1`) and `TF_STATE_ENDPOINT`
(`https://<namespace>.compat.objectstorage.<region>.oraclecloud.com`), and the key as the secrets `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`, in the repo's Settings > Secrets and variables > Actions. The workflow sets
`AWS_REQUEST_CHECKSUM_CALCULATION` and `AWS_RESPONSE_CHECKSUM_VALIDATION` to `when_required`, which Oracle's S3 API needs
(export both to run Terraform from your own machine). State locking is off, so the workflow's concurrency group keeps
applies to one at a time. It also needs a Cloudflare API token, `CLOUDFLARE_API_TOKEN`:

- a repository secret, a **Zone:DNS:Read** token for `nikolaivorkinn.com`, which plans use;
- a secret of each of the `terraform` (dev/QA) and `terraform-production` environments, a **Zone:DNS:Edit** token for
  the same zone, which apply uses.

(Create them under My Profile > API Tokens > Create Token > Edit zone DNS, limited to the one zone.)
