# The A records for the names one environment's Caddy serves. The names come from config/<environment>.env, the file
# the deploy bundle's settings.env is made from, so DNS and Caddy can't disagree about them. They are DNS-only: the
# firmware pins Let's Encrypt's roots, so nothing may sit in front of the servers and swap in another CA (see the
# firmware's README). Dev/QA and production are applied separately (the Release and Promote workflows), each with its
# own state.
locals {
  settings = { for line in regexall("(?m)^([A-Z][A-Z0-9_]*)=(.*)$", file("${path.module}/../../config/${var.environment}.env")) : line[0] => trimspace(line[1]) }

  # THE MAX MIX's name, for the environments that host it (dev/QA so far); none elsewhere.
  maxmix_hosts = compact([lookup(local.settings, "MAXMIX_HOST", "")])

  # APEX_HOST and www. redirect to AUTH_HOST (config/*.env), but all of them need a record.
  names = toset(concat(
    [local.settings.APEX_HOST, "www.${local.settings.APEX_HOST}", local.settings.AUTH_HOST, local.settings.SSO_HOST, local.settings.LOGS_HOST, local.settings.METRICS_HOST],
    local.maxmix_hosts,
  ))
}

resource "cloudflare_dns_record" "host" {
  for_each = local.names

  zone_id = var.zone_id
  name    = each.value
  type    = "A"
  content = var.host_ips[var.environment]
  ttl     = 300
  proxied = false
  comment = "${var.environment} (managed by Terraform, infra/cloudflare)"
}
