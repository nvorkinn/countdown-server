# Which environment this is (dev or prod): the hostnames it serves come from the same file the deploy bundle's
# settings.env is made from, config/<environment>.env, so Caddy and this config can't disagree about them. Its state and
# secrets are the workflow's (terraform.yml).
locals {
  settings      = { for line in regexall("(?m)^([A-Z][A-Z0-9_]*)=(.*)$", file("${path.module}/../config/${var.environment}.env")) : line[0] => trimspace(line[1]) }
  authentik_url = coalesce(var.authentik_url, "https://${local.settings.SSO_HOST}")
}
