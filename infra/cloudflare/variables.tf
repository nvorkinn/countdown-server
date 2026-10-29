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
