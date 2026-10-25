# State lives in the same Oracle Cloud Object Storage bucket as terraform/'s (see its backend.tf and README.md for the
# GitHub variables and secrets that reach it), under its own key: DNS is one zone shared by both environments, so
# there is a single state, not one per environment.
terraform {
  backend "s3" {
    key = "cloudflare/terraform.tfstate"

    use_path_style              = true
    skip_region_validation      = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
  }
}
