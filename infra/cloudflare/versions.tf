# Terraform itself is pinned here and in TF_VERSION in .github/workflows/cloudflare.yml.
terraform {
  required_version = "~> 1.16.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.19"
    }
  }
}
