# The A records for the names each environment's Caddy serves. The names come from config/<environment>.env, the file
# the deploy bundle's settings.env is made from, so DNS and Caddy can't disagree about them. They are DNS-only: the
# firmware pins Let's Encrypt's roots, so nothing may sit in front of the servers and swap in another CA (see the
# firmware's README).
locals {
  settings = {
    for environment in keys(var.host_ips) : environment => {
      for line in regexall("(?m)^([A-Z][A-Z0-9_]*)=(.*)$", file("${path.module}/../../config/${environment}.env")) : line[0] => trimspace(line[1])
    }
  }

  # APEX_HOST and www. redirect to AUTH_HOST (config/*.env), but all of them need a record.
  records = merge([
    for environment, settings in local.settings : {
      for name in [settings.APEX_HOST, "www.${settings.APEX_HOST}", settings.AUTH_HOST, settings.SSO_HOST, settings.LOGS_HOST, settings.METRICS_HOST] :
      "${environment}/${name}" => { environment = environment, name = name }
    }
  ]...)
}

resource "cloudflare_dns_record" "host" {
  for_each = local.records

  zone_id = var.zone_id
  name    = each.value.name
  type    = "A"
  content = var.host_ips[each.value.environment]
  ttl     = 300
  proxied = false
  comment = "${each.value.environment} (managed by Terraform, infra/cloudflare)"
}
