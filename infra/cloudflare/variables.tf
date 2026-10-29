variable "zone_id" {
  type        = string
  description = "The Cloudflare zone for the domain (not a secret)."
  default     = "edde009a4040ff9e41b3a880e8ccde41" # nikolaivorkinn.com
}

variable "environment" {
  type        = string
  description = "Which environment's records this state manages: dev (dev/QA) or prod. Each has its own state and apply."

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "The environment is dev or prod."
  }
}

variable "host_ips" {
  type        = map(string)
  description = "Each environment's host, the address its names point at; only var.environment's is used. To cut over, change it here."
  default = {
    dev  = "140.238.67.107" # dev/QA
    prod = "130.162.189.60" # production
  }
}

variable "account_id" {
  type        = string
  description = "The Cloudflare account that owns the zone and its Zero Trust (Access) setup (not a secret)."
  default     = "1b9f3a9bf1519b6b51a19f194df0b0bd"
}

variable "access_emails" {
  type        = list(string)
  description = "The addresses Cloudflare Access lets into THE MAX MIX, each proving itself with a one-time PIN."
  default     = ["n.vorkinn@gmail.com"]
}

variable "access_session_duration" {
  type        = string
  description = "How long an Access login lasts before it asks again."
  default     = "24h"
}
