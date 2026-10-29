# The A records for the names one environment's Caddy serves, and on dev/QA THE MAX MIX's CNAME to Vercel. The names
# come from config/<environment>.env, the file the deploy bundle's settings.env is made from, so DNS and Caddy can't
# disagree about them. They are DNS-only: the firmware pins Let's Encrypt's roots, so nothing may sit in front of the
# servers and swap in another CA (see the firmware's README). Dev/QA and production are applied separately (the Release
# and Promote workflows), each with its own state.
locals {
  settings = { for line in regexall("(?m)^([A-Z][A-Z0-9_]*)=(.*)$", file("${path.module}/../../config/${var.environment}.env")) : line[0] => trimspace(line[1]) }

  # THE MAX MIX's name, for the environments that have one (dev/QA so far); none elsewhere. It is hosted on Vercel, not
  # on this server, so it gets a CNAME to Vercel instead of an A record to the host.
  maxmix_hosts = compact([lookup(local.settings, "MAXMIX_HOST", "")])

  # APEX_HOST and www. redirect to AUTH_HOST (config/*.env), but all of them need a record.
  server_names = [local.settings.APEX_HOST, "www.${local.settings.APEX_HOST}", local.settings.AUTH_HOST, local.settings.SSO_HOST, local.settings.LOGS_HOST, local.settings.METRICS_HOST]

  records = merge(
    { for name in local.server_names : name => { type = "A", content = var.host_ips[var.environment], note = "" } },
    # DNS-only (Vercel asks for it): Cloudflare must not proxy it.
    { for name in local.maxmix_hosts : name => { type = "CNAME", content = "cname.vercel-dns.com", note = ": THE MAX MIX on Vercel" } },
  )
}

resource "cloudflare_dns_record" "host" {
  for_each = local.records

  zone_id = var.zone_id
  name    = each.key
  type    = each.value.type
  content = each.value.content
  ttl     = 300
  proxied = false
  comment = "${var.environment} (managed by Terraform, infra/cloudflare)${each.value.note}"
}
