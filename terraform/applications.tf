# Forward-auth for the Broker, Logs and Metrics through the embedded outpost. No group binding on the Broker: any
# signed-in user may use the broker's pairing and config pages, which is what lets the sign-up reach /pair. Logs
# and Metrics are for the authentik Admins group only.
locals {
  proxy_providers = {
    logs    = { name = "Provider for Logs", host = "https://${local.settings.LOGS_HOST}" }
    metrics = { name = "Provider for Metrics", host = "https://${local.settings.METRICS_HOST}" }
    broker  = { name = "Provider for Broker", host = "https://${local.settings.AUTH_HOST}" }
  }

  oauth_scopes = [
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-email",
    "goauthentik.io/providers/oauth2/scope-profile",
    "goauthentik.io/providers/oauth2/scope-entitlements",
    "goauthentik.io/providers/proxy/scope-proxy",
  ]
}

data "authentik_property_mapping_provider_scope" "proxy" {
  managed_list = local.oauth_scopes
}

resource "authentik_provider_proxy" "this" {
  for_each = local.proxy_providers

  name                  = each.value.name
  mode                  = "forward_single"
  external_host         = each.value.host
  intercept_header_auth = true
  authorization_flow    = local.ids.flows["default-provider-authorization-implicit-consent"]
  invalidation_flow     = local.ids.flows["default-provider-invalidation-flow"]
  property_mappings     = data.authentik_property_mapping_provider_scope.proxy.ids

  # Not owned by this config, so left as Authentik has it.
  lifecycle {
    ignore_changes = [access_token_validity]
  }
}

# A proxy provider is also an OAuth2 provider (one object in Authentik). Logs and Metrics have a fixed client id and
# secret, and this resource manages their OAuth2 side too: it is a second resource on the same provider, which is why
# these are imported with the same ids as the proxy ones. The only grant wanted is the authorization code, all a
# forward-auth proxy uses: password would skip the second factor, and client_credentials isn't used here. But
# authentik's proxy app re-applies its defaults (all three grants) to every proxy provider when it reconciles, on start
# and now and then after, with no audit event, so the live grants can't be held at this and are not managed (see
# ignore_changes below). Keep the client secrets private: with one, the extra grants can be used.
# The redirect URIs are listed here too: Authentik derives them from each proxy provider's external host, and
# this resource would otherwise blank them. The Broker's provider has nothing pinned, so it has no entry.
locals {
  oauth_clients = {
    logs = {
      client_id     = "8Knwigt49WR5g5n3HpJ2Z1NwUZDEIXklKb4gXj55"
      client_secret = var.logs_client_secret
    }
    metrics = {
      client_id     = "8Q9k5OULc2hhSC7Fs67ApkxhUncoPONpRyHQPgFa"
      client_secret = var.metrics_client_secret
    }
  }
}

resource "authentik_provider_oauth2" "this" {
  for_each = local.oauth_clients

  name               = local.proxy_providers[each.key].name
  client_type        = "confidential"
  client_id          = each.value.client_id
  client_secret      = each.value.client_secret
  grant_types        = ["authorization_code"]
  authorization_flow = local.ids.flows["default-provider-authorization-implicit-consent"]
  invalidation_flow  = local.ids.flows["default-provider-invalidation-flow"]
  allowed_redirect_uris = [
    for url in [
      "${local.proxy_providers[each.key].host}/outpost.goauthentik.io/callback?X-authentik-auth-callback=true",
      "${local.proxy_providers[each.key].host}?X-authentik-auth-callback=true",
    ] : { matching_mode = "strict", redirect_uri_type = "authorization", url = url }
  ]

  # Updating a proxy provider resets its grants to the defaults, so this must be applied after the proxy resource.
  depends_on = [authentik_provider_proxy.this]

  # The proxy resource sets the scope mappings; the token validity isn't pinned. The grants are set only when this is
  # created: authentik puts its defaults back, and a plan that undoes that asks for an approval every time.
  lifecycle {
    ignore_changes = [property_mappings, access_token_validity, grant_types]
  }
}

# The provider is the proxy one above (same object, same id), so this can't be created, only imported. The id is
# looked up from the live server, so a new environment has to create the proxy providers first (an apply targeting
# authentik_provider_proxy.this), and only then does a plan find them to import.
import {
  for_each = local.oauth_clients

  to = authentik_provider_oauth2.this[each.key]
  id = local.ids.providers[local.proxy_providers[each.key].name]
}

resource "authentik_application" "broker" {
  name              = "Broker"
  slug              = "broker"
  meta_icon         = "/static/dist/assets/countdown/countdown-pill.svg"
  protocol_provider = authentik_provider_proxy.this["broker"].id
}

resource "authentik_application" "logs" {
  name              = "Logs"
  slug              = "logs"
  meta_icon         = "/static/dist/assets/countdown/countdown-pill.svg"
  protocol_provider = authentik_provider_proxy.this["logs"].id
}

resource "authentik_application" "metrics" {
  name              = "Metrics"
  slug              = "metrics"
  meta_icon         = "/static/dist/assets/countdown/countdown-pill.svg"
  protocol_provider = authentik_provider_proxy.this["metrics"].id
}

resource "authentik_policy_binding" "logs_admins" {
  target  = authentik_application.logs.uuid
  group   = local.ids.groups["authentik Admins"]
  order   = 0
  enabled = true
  negate  = false
  timeout = 30
}

resource "authentik_policy_binding" "metrics_admins" {
  target  = authentik_application.metrics.uuid
  group   = local.ids.groups["authentik Admins"]
  order   = 0
  enabled = true
  negate  = false
  timeout = 30
}

resource "authentik_outpost" "embedded" {
  name               = "authentik Embedded Outpost"
  type               = "proxy"
  protocol_providers = [for key in ["logs", "metrics", "broker"] : authentik_provider_proxy.this[key].id]
  config = jsonencode({
    authentik_host = "https://${local.settings.SSO_HOST}"
  })
}

import {
  to = authentik_outpost.embedded
  id = local.ids.outposts["authentik Embedded Outpost"]
}
