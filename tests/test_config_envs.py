import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "config"


def _settings(path: Path) -> dict[str, str]:
    lines = (line for line in path.read_text().splitlines() if line and not line.startswith("#"))
    return dict(line.split("=", 1) for line in lines)


def test_every_environment_sets_the_same_things():
    """Else a release to the environment that lacks one starts with a setting empty."""
    environments = {path.stem: _settings(path) for path in CONFIG.glob("*.env")}

    assert {"dev", "prod"} <= environments.keys()
    assert len({frozenset(settings) for settings in environments.values()}) == 1
    assert all(value for settings in environments.values() for value in settings.values())


def test_each_environment_has_secrets_to_match():
    """Each target's bundle is built from config/<target>.env and secrets/<target>/, so a target needs both."""
    assert {path.stem for path in CONFIG.glob("*.env")} >= {
        path.name for path in (ROOT / "secrets").iterdir() if path.is_dir()
    }


def test_every_name_the_compose_labels_and_settings_use_is_a_setting_or_secret():
    """A name in neither would be empty in production, and a site with no address won't load."""
    compose = (ROOT / "docker-compose.yml").read_text()
    names = set(re.findall(r"\$\{([A-Z_]+)[:}?-]", compose))
    known = set(_settings(CONFIG / "dev.env")) | {
        "COMPOSE_PROFILES",
        "EDGE_NETWORK_EXTERNAL",
        "EDGE_TRUSTED_PROXIES",
        "COUNTDOWN_SERVER_VERSION",
        "CADDY_VERSION",
        "FLUENT_BIT_VERSION",
        "VICTORIA_LOGS_VERSION",
        "VICTORIA_METRICS_VERSION",
    }

    assert names <= known


def test_cloudflare_only_reads_settings_the_environments_have():
    """infra/cloudflare reads the hostnames from config/<environment>.env; a name they lack fails its plan."""
    used = {
        name
        for path in (ROOT / "infra" / "cloudflare").glob("*.tf")
        for name in re.findall(r"local\.settings\.([A-Z][A-Z0-9_]*)", path.read_text())
    }

    assert used
    assert used <= set(_settings(CONFIG / "prod.env"))
    assert used <= set(_settings(CONFIG / "dev.env"))


def test_only_prod_owns_the_edge():
    assert _settings(CONFIG / "prod.env")["EDGE_OWNED"] == "true"
    assert _settings(CONFIG / "dev.env")["EDGE_OWNED"] == "false"
