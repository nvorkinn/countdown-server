# Terraform itself is pinned here and in TF_VERSION in .github/workflows/cloudflare.yml, and the same as terraform/
# (the Authentik config); bump them together.
terraform {
  required_version = "~> 1.16.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.19"
    }
  }
}
