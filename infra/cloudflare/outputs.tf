# The Access application's audience tag. THE MAX MIX checks it in the Access token (MAXMIX_ACCESS_AUD in
# config/<environment>.env), so copy it there after the first apply (Zero Trust > Access > Applications shows it too).
output "maxmix_access_aud" {
  description = "The audience (AUD) tag of THE MAX MIX's Access application, empty where it isn't hosted."
  value       = one(cloudflare_zero_trust_access_application.maxmix[*].aud)
}
