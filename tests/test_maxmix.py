"""THE MAX MIX (github.com/nvorkinn/the-max-mix) is hosted on Vercel: this repository only owns its DNS name."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
DEV = ROOT / "config" / "dev.env"
PROD = ROOT / "config" / "prod.env"
DNS = (ROOT / "infra" / "cloudflare" / "main.tf").read_text()


def test_nothing_of_it_runs_here():
    """It is static files on Vercel: no container, no volume, no port."""
    assert "the-max-mix" not in COMPOSE["services"]
    assert not any("max" in name and "mix" in name for name in COMPOSE.get("volumes", {}))
    assert "the-max-mix" not in (ROOT / "docker-compose.yml").read_text()


def test_caddy_has_no_site_for_it():
    assert "MAXMIX" not in (ROOT / "caddy" / "Caddyfile").read_text()


def test_it_is_not_deployed():
    """Neither deploy-services.yml's matrix (which promote.yml reuses) nor deploy.sh knows the service."""
    workflow = (ROOT / ".github" / "workflows" / "deploy-services.yml").read_text()
    assert "the-max-mix" not in workflow
    assert "the-max-mix" not in (ROOT / "scripts" / "deploy.sh").read_text()
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert "the-max-mix" not in path.read_text(), path.name


def test_authentik_no_longer_guards_it():
    for path in (ROOT / "terraform").glob("*.tf"):
        assert "maxmix" not in path.read_text().lower(), path.name


def test_its_host_is_set_for_dev_qa_only():
    """infra/cloudflare takes the name from MAXMIX_HOST, which production does not have."""
    assert re.search(r"^MAXMIX_HOST=\S+$", DEV.read_text(), re.MULTILINE)
    assert not re.search(r"^MAXMIX_", PROD.read_text(), re.MULTILINE)


def test_its_dns_record_is_a_dns_only_cname_to_vercel():
    """Vercel asks for a record Cloudflare does not proxy; the name is built only where MAXMIX_HOST is set."""
    assert 'lookup(local.settings, "MAXMIX_HOST", "")' in DNS
    assert re.search(r'type = "CNAME", content = "cname\.vercel-dns\.com"', DNS)
    assert "for name in local.maxmix_hosts" in DNS
    resource = DNS.split('resource "cloudflare_dns_record" "host" {', 1)[1]
    assert "proxied = false" in resource
    assert "proxied = true" not in DNS
