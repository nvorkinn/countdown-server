import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "config"
# Settings only dev/QA has, because it alone runs THE MAX MIX (docker-compose.yml's the-max-mix, deploy-services.yml).
# Caddy gives its site an inert default where MAXMIX_HOST is unset, and infra/cloudflare and terraform/ skip it.
DEV_ONLY = {"MAXMIX_HOST"}


def _settings(path: Path) -> dict[str, str]:
    lines = (line for line in path.read_text().splitlines() if line and not line.startswith("#"))
    return dict(line.split("=", 1) for line in lines)


def test_every_environment_sets_the_same_things():
    """Else a release to the environment that lacks one starts with a setting empty."""
    environments = {path.stem: _settings(path) for path in CONFIG.glob("*.env")}

    assert {"dev", "prod"} <= environments.keys()
    assert len({frozenset(settings.keys() - DEV_ONLY) for settings in environments.values()}) == 1
    assert all(value for settings in environments.values() for value in settings.values())


def test_only_dev_has_the_dev_only_settings():
    """Production is promoted from the same compose file and Caddyfile; its host mustn't pick THE MAX MIX up unasked."""
    assert _settings(CONFIG / "dev.env").keys() >= DEV_ONLY
    assert not DEV_ONLY & _settings(CONFIG / "prod.env").keys()


def test_each_environment_has_secrets_to_match():
    """Each target's bundle is built from config/<target>.env and secrets/<target>/, so a target needs both."""
    assert {path.stem for path in CONFIG.glob("*.env")} >= {
        path.name for path in (ROOT / "secrets").iterdir() if path.is_dir()
    }


def test_the_caddyfile_s_placeholders_are_all_settings_or_secrets():
    """A placeholder in neither would be empty in production, and a site with no address won't load."""
    placeholders = set(re.findall(r"\{\$([A-Z_]+)(?::[^}]*)?\}", (ROOT / "caddy" / "Caddyfile").read_text()))
    known = set(_settings(CONFIG / "dev.env")) | {"LOGS_INGEST_TOKEN", "LOG_REQUEST_HEADERS"}

    assert placeholders <= known


def test_terraform_only_reads_settings_the_environments_have():
    """terraform/ reads the hostnames from config/<environment>.env; a name it uses that they lack fails its plan."""
    used = {
        name
        for path in (ROOT / "terraform").glob("*.tf")
        for name in re.findall(r"local\.settings\.([A-Z][A-Z0-9_]*)", path.read_text())
    }

    assert used
    assert used <= set(_settings(CONFIG / "prod.env"))
    assert used <= set(_settings(CONFIG / "dev.env"))
