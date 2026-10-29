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
    """Its port is loopback's only: Caddy, with host networking, is the way in (and authentik guards that)."""
    assert SERVICE["ports"] == ["127.0.0.1:3000:3000"]
    assert "network_mode" not in SERVICE


def test_caddy_serves_its_host_to_the_authentik_admins_only_and_has_an_inert_default():
    """It has no sign-in of its own; and production has no MAXMIX_HOST, yet a site without an address stops Caddy."""
    site = _site("{$MAXMIX_HOST:maxmix.localhost}")
    assert "import admin_only 127.0.0.1:3000" in site
    assert "reverse_proxy" not in site.replace("import admin_only", "")


def test_the_app_is_given_its_public_address_from_the_host_setting():
    """The app reads PUBLIC_URL, which its command builds from settings.env's MAXMIX_HOST."""
    command = " ".join(SERVICE["command"])
    assert 'PUBLIC_URL="https://$$MAXMIX_HOST"' in command
    assert re.search(r"^MAXMIX_HOST=\S+$", DEV.read_text(), re.MULTILINE)
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
