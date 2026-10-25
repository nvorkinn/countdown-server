# The API token comes from the CLOUDFLARE_API_TOKEN environment variable, never from a file or a variable. It needs
# Zone:DNS:Edit on the zone below (Zone:DNS:Read is enough to plan).
provider "cloudflare" {}
