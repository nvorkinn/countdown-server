"""Run the settings site locally, signed in, with one screen of each type to set up: no Redis, login or hardware.

It starts the broker on a fresh database in a temporary folder, with an in-memory Redis (fakeredis), and pairs a
user with an e-paper screen (480x800), an HD one (1872x1404) and a touchscreen (1280x800), as their registrations and
pairing would. Every request is signed in as that user, as the login proxy would send it (its Remote-* headers), so
open http://127.0.0.1:5000 and every screen is in the sidebar.

Nothing outside is needed or kept: settings the broker needs from the environment get stand-ins, and the database
goes when the server stops. Searches for stops and weather places still call TfL, Entur and Open-Meteo.

Usage: uv run python scripts/dev_server.py [--port 5000] [--user dev]
"""

import argparse
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import fakeredis
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from broker import create_app
from broker.auth import hash_secret
from broker.db import db
from broker.models import Device
from broker.routes.pages import _write_subscriptions_to_redis
from broker.services import assignments, config_store, registration
from broker.services.registration import Screen

# The screens paired with the user, each with its name.
SCREENS = (
    (Screen("epaper", 480, 800, "mono-1bpp-packed"), "Hallway"),
    (Screen("epaper", 1872, 1404, "png"), "Kitchen HD"),
    (Screen("touchscreen", 1280, 800), "Living room tablet"),
)


def main() -> None:
    """Start the site on a fresh database, with the screens paired, signed in as the user."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--user", default="dev", help="the signed-in user's id (Remote-Sub)")
    args = parser.parse_args()

    folder = Path(tempfile.mkdtemp(prefix="countdown-dev-"))
    for name, value in {
        "FLASK_SECRET_KEY": "dev",
        "SPOTIFY_CLIENT_ID": "dev",
        "SPOTIFY_CLIENT_SECRET": "dev",
        "SPOTIFY_REDIRECT_URI": f"http://127.0.0.1:{args.port}/auth/spotify/callback",
        "CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ENVIRONMENT": "dev",
    }.items():
        os.environ.setdefault(name, value)
    os.environ["BROKER_DB_PATH"] = str(folder / "broker.db")

    app = create_app()
    server = fakeredis.FakeServer()
    app.extensions["redis"] = fakeredis.FakeRedis(server=server, decode_responses=True)
    app.extensions["redis_bytes"] = fakeredis.FakeRedis(server=server)
    with app.app_context():
        for device_id, name in _seed(args.user):
            print(f"{name}: http://127.0.0.1:{args.port}/device/{device_id}")

    app.wsgi_app = _signed_in(app.wsgi_app, args.user)
    app.run(port=args.port)


def _seed(user: str) -> list[tuple[str, str]]:
    """Register SCREENS and pair them with the user, as pairing does; return each one's (device id, name)."""
    seeded = []
    for index, (screen, name) in enumerate(SCREENS):
        found = registration.register_screen(hash_secret(f"dev-screen-secret-{index:02}"), screen)
        if found is None:  # pragma: no cover -- a fresh database has no screens waiting
            msg = "couldn't register the screens"
            raise RuntimeError(msg)
        device = db.session.get_one(Device, found.device_id)
        device.owner = user
        device.paired_at = datetime.now(UTC)
        device.device_name = name
        device.pairing_codes.clear()
        assignments.auto_name(device)
        seeded.append((device.device_id, name))
    db.session.commit()
    config_store.publish_all()
    _write_subscriptions_to_redis()
    return seeded


def _signed_in(wsgi_app, user: str):  # noqa: ANN001, ANN202 -- a WSGI app in and out
    """Return the app with every request signed in as the user, as the login proxy's headers would say."""

    def app(environ, start_response):  # noqa: ANN001, ANN202
        environ.setdefault("HTTP_REMOTE_SUB", user)
        environ.setdefault("HTTP_REMOTE_NAME", user)
        return wsgi_app(environ, start_response)

    return app


if __name__ == "__main__":
    main()
