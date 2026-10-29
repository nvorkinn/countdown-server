# The A records for the names one environment's Caddy serves. The names come from config/<environment>.env, the file
# the deploy bundle's settings.env is made from, so DNS and Caddy can't disagree about them. They are DNS-only: the
# firmware pins Let's Encrypt's roots, so nothing may sit in front of the servers and swap in another CA (see the
# firmware's README). The one exception is MAXMIX_HOST (THE MAX MIX, a browser app, no firmware): it is proxied, since
# Cloudflare Access is enforced by the proxy, and Access guards it. Dev/QA and production are applied separately (the
# Release and Promote workflows), each with its own state.
locals {
  settings = { for line in regexall("(?m)^([A-Z][A-Z0-9_]*)=(.*)$", file("${path.module}/../../config/${var.environment}.env")) : line[0] => trimspace(line[1]) }

  # THE MAX MIX's name, for the environments that host it (dev/QA so far); empty elsewhere.
  maxmix_host = lookup(local.settings, "MAXMIX_HOST", "")

  # APEX_HOST and www. redirect to AUTH_HOST (config/*.env), but all of them need a record.
  names = toset([local.settings.APEX_HOST, "www.${local.settings.APEX_HOST}", local.settings.AUTH_HOST, local.settings.SSO_HOST, local.settings.LOGS_HOST, local.settings.METRICS_HOST])
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

# THE MAX MIX's name. Proxied (orange cloud), unlike the rest: Cloudflare Access sits in the proxy. Caddy still gets its
# certificate, as the zone's SSL mode is Full and "Always use HTTPS" is off, so the HTTP-01 challenge passes through.
resource "cloudflare_dns_record" "maxmix" {
  count = local.maxmix_host == "" ? 0 : 1

  zone_id = var.zone_id
  name    = local.maxmix_host
  type    = "A"
  content = var.host_ips[var.environment]
  ttl     = 1 # automatic, the only value for a proxied record
  proxied = true
  comment = "${var.environment} THE MAX MIX, behind Access (managed by Terraform, infra/cloudflare)"
}

# Who may open it: the people listed in var.access_emails, who prove it with a one-time PIN sent to the address. The
# app also checks the Access token itself, so a request that reaches Caddy without passing Access is refused.
resource "cloudflare_zero_trust_access_policy" "maxmix" {
  count = local.maxmix_host == "" ? 0 : 1

  account_id = var.account_id
  name       = "THE MAX MIX (${var.environment}): owners"
  decision   = "allow"
  include    = [for email in var.access_emails : { email = { email = email } }]
}

resource "cloudflare_zero_trust_access_application" "maxmix" {
  count = local.maxmix_host == "" ? 0 : 1

  account_id           = var.account_id
  name                 = "THE MAX MIX (${var.environment})"
  domain               = local.maxmix_host
  type                 = "self_hosted"
  session_duration     = var.access_session_duration
  app_launcher_visible = false
  policies             = [{ id = cloudflare_zero_trust_access_policy.maxmix[0].id, precedence = 1 }]
}
