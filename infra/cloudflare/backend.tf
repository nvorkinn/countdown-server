# State lives in the same Oracle Cloud Object Storage bucket as terraform/'s (see its backend.tf and README.md for the
# GitHub variables and secrets that reach it), under its own keys. The zone is shared, but each environment's records
# are applied by its own workflow, so each has its own state.
terraform {
  backend "s3" {
    # Dev/QA's. Production's is passed at init (-backend-config="key=cloudflare/prod/terraform.tfstate").
    key = "cloudflare/terraform.tfstate"

    use_path_style              = true
    skip_region_validation      = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
  }
}
