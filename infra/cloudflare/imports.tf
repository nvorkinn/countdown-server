# These records already exist (made by hand and through the API during the cutover), so each environment's first plan
# imports its own instead of creating them. Once an apply has put them in the state the import block does nothing and can be deleted.
locals {
  existing_record_ids = {
    "dev/dev.nikolaivorkinn.com"         = "eb27419686ff0fb910ca02c75d4ec1ec"
    "dev/www.dev.nikolaivorkinn.com"     = "d019f8bbd41fca7903e1cfb35365fd85"
    "dev/auth.dev.nikolaivorkinn.com"    = "1745583871cb59605cd52496b08b17ed"
    "dev/sso.dev.nikolaivorkinn.com"     = "1091999e7eda10f3cd76128f92ba2da4"
    "dev/logs.dev.nikolaivorkinn.com"    = "2e33c45dcb92d2a89891c26fc86db93c"
    "dev/metrics.dev.nikolaivorkinn.com" = "15466e50f044a67be1f811479a92bd11"
    "prod/nikolaivorkinn.com"            = "c4eb9e8322731955a831abebc9f1d0ec"
    "prod/www.nikolaivorkinn.com"        = "d05b03750bfd0bb4d245010f9c2bea3c"
    "prod/auth.nikolaivorkinn.com"       = "508d6fbaf7ccb430ba76f40ed7305305"
    "prod/sso.nikolaivorkinn.com"        = "ee799f498944a366e71df20f35b4b151"
    "prod/logs.nikolaivorkinn.com"       = "b092553d5953627cc2f3bcab749ca556"
    "prod/metrics.nikolaivorkinn.com"    = "b4a9e5c56e8eb9698db5f5e9f813d761"
  }
}

import {
  for_each = { for key, id in local.existing_record_ids : trimprefix(key, "${var.environment}/") => id if startswith(key, "${var.environment}/") }

  to = cloudflare_dns_record.host[each.key]
  id = "${var.zone_id}/${each.value}"
}
