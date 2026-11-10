variable "zone_ids" {
  type        = map(string)
  description = "Each environment's Cloudflare zone (not secret): its names are all in its own domain. Only var.environment's is used."
  default = {
    dev  = "TODO_COUNTDOWN_BUILD_ZONE_ID" # countdown.build
    prod = "TODO_COUNTDOWN_RUN_ZONE_ID"   # countdown.run
  }
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
    dev  = "140.238.67.107" # the old Oracle dev/QA host; unused while cname_targets has dev
    prod = "130.162.189.60" # production
  }
}

variable "cname_targets" {
  type        = map(string)
  description = "An environment listed here gets CNAME records to this name instead of A records to host_ips. Dev/QA runs on the home server, whose public IP home-stack's cloudflare-ddns keeps current on *.vorkinn.org."
  default = {
    dev = "home.vorkinn.org"
  }
}
