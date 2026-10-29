"""THE MAX MIX (github.com/nvorkinn/the-max-mix) on dev/QA: its compose service, Caddy site and deploy."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICE = COMPOSE["services"]["the-max-mix"]
CADDYFILE = (ROOT / "caddy" / "Caddyfile").read_text()
DEV = ROOT / "config" / "dev.env"
PROD = ROOT / "config" / "prod.env"


def _site(address: str) -> str:
    return CADDYFILE.split(address + " {", 1)[1].split("\n}\n", 1)[0]


def test_it_runs_the_latest_image_and_pulls_it_on_every_deploy():
    """A dev/QA release is meant to bring the newest build, not one pinned to this repository's tag."""
    assert SERVICE["image"] == "ghcr.io/nvorkinn/the-max-mix:latest"
    assert SERVICE["pull_policy"] == "always"


def test_only_caddy_can_reach_it():
    """Its port is loopback's only: Caddy, with host networking, is the way in (and Access guards that)."""
    assert SERVICE["ports"] == ["127.0.0.1:3000:3000"]
    assert "network_mode" not in SERVICE


def test_caddy_proxies_its_host_to_that_port_and_has_an_inert_default():
    """Production has no MAXMIX_HOST, and a site with no address would stop Caddy loading."""
    site = _site("{$MAXMIX_HOST:maxmix.localhost}")
    assert "reverse_proxy 127.0.0.1:3000" in site


def test_every_setting_it_maps_is_in_the_dev_settings():
    """The app reads PUBLIC_URL and ACCESS_*, which its command builds from settings.env's MAXMIX_* values."""
    mapped = set(re.findall(r"\$\$(MAXMIX_[A-Z_]+)", " ".join(SERVICE["command"])))
    settings = set(re.findall(r"^(MAXMIX_[A-Z_]+)=", DEV.read_text(), re.MULTILINE))
    assert mapped == {"MAXMIX_HOST", "MAXMIX_ACCESS_TEAM_DOMAIN", "MAXMIX_ACCESS_AUD"}
    assert mapped <= settings
    assert "settings.env" in SERVICE["env_file"]


def test_its_login_survives_a_new_image():
    volumes = SERVICE["volumes"]
    assert volumes == ["the_max_mix_data:/data"]
    assert COMPOSE["volumes"]["the_max_mix_data"] == {"name": "the_max_mix_data"}


def test_production_does_not_deploy_it():
    """promote.yml deploys the same matrix, so the-max-mix is in it for dev/QA only."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy-services.yml").read_text())
    expression = workflow["jobs"]["deploy"]["strategy"]["matrix"]["service"]
    dev, prod = re.findall(r"'(\[[^']*\])'", expression)
    assert "the-max-mix" in dev
    assert "the-max-mix" not in prod
    assert set(re.findall(r'"([^"]+)"', dev)) - set(re.findall(r'"([^"]+)"', prod)) == {"the-max-mix"}
    assert "inputs.target == 'dev'" in expression
    assert not re.search(r"^MAXMIX_", PROD.read_text(), re.MULTILINE)
