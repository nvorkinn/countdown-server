variable "zone_id" {
  type        = string
  description = "The Cloudflare zone for the domain (not a secret)."
  default     = "edde009a4040ff9e41b3a880e8ccde41" # nikolaivorkinn.com
}

variable "host_ips" {
  type        = map(string)
  description = "Each environment's host, the address its names point at. To cut over, change it here."
  default = {
    dev  = "140.238.67.107" # dev/QA
    prod = "130.162.189.60" # production
  }
}
