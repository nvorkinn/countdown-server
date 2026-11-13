# The records for the names one environment's Caddy serves: A records to the host's address in production, CNAMEs to the
# home server in dev/QA (see var.cname_targets). The names come from config/<environment>.env, the file
# the deploy bundle's settings.env is made from, so DNS and Caddy can't disagree about them. They are DNS-only: the
# firmware pins Let's Encrypt's roots, so nothing may sit in front of the servers and swap in another CA (see the
# firmware's README). Dev and production are applied separately (the Cloudflare workflow, by hand), each with its
# own state.
locals {
  settings = { for line in regexall("(?m)^([A-Z][A-Z0-9_]*)=(.*)$", file("${path.module}/../../config/${var.environment}.env")) : line[0] => trimspace(line[1]) }

  # Where the environment's names point: a CNAME target (dev/QA, on the home server) or, without one, an A record to the
  # environment's address.
  cname_target = lookup(var.cname_targets, var.environment, null)

  # APEX_HOST and www. redirect to APP_HOST (config/*.env), but all of them need a record. API_HOST is the screens' API,
  # and LEGACY_HOST the name the settings site and the API shared before (still the API for boards flashed then).
  # TAUTH_HOST is Tinyauth and ID_HOST Pocket ID, which every environment runs for itself.
  names = toset([
    local.settings.APEX_HOST, "www.${local.settings.APEX_HOST}", local.settings.APP_HOST, local.settings.API_HOST,
    local.settings.LEGACY_HOST, local.settings.LOGS_HOST, local.settings.METRICS_HOST, local.settings.TAUTH_HOST,
    local.settings.ID_HOST,
  ])
}

resource "cloudflare_dns_record" "host" {
  for_each = local.names

  zone_id = var.zone_ids[var.environment]
  name    = each.value
  type    = local.cname_target == null ? "A" : "CNAME"
  content = local.cname_target == null ? var.host_ips[var.environment] : local.cname_target
  ttl     = 300
  proxied = false
  comment = "${var.environment} (managed by Terraform, infra/cloudflare)"
}
