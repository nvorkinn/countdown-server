"""The broker's side of the store contract (countdown-api's store.yaml, the vendor/countdown-api submodule).

Every key the broker writes or reads that countdown reads or writes is the contract's, with the contract's TTL, and
what it writes is the contract's canonical example (countdown_api.fixtures), which countdown's tests read too. The
other modules' tests round-trip most of the values through those examples (test_config_store, test_pairing_key,
test_render_screens, test_render_jobs, test_frame_record, test_glow, test_music_provider, test_album_art,
test_developer_provider, test_weather_place); here are the subscriptions, the places beside them, the pace hints, and
the keys themselves.
"""

import json
from datetime import UTC, datetime, timedelta

import fakeredis
from countdown_api import fixtures, scripts
from countdown_api import keys as store_keys
from countdown_api.models import BackgroundPlace

from broker import album_art
from broker.db import db
from broker.models import Device, DeviceConfig, GlowmarktCredentials, ParcelKey
from broker.providers import developer, glow, loop, music, parcel
from broker.routes import pages
from broker.services import config_store, device_keys, frame_record, pace, pairing, pairing_key, render_jobs
from tests.conftest import account_of

CONFIG = fixtures.load("config_store.json")
PAIRING = fixtures.load("pairing_key.json")
SUBSCRIPTIONS = fixtures.load("subscriptions.json")
TOUCHSCREEN = fixtures.load("touchscreen.json")
PLACE = fixtures.load("background_place.json")
OSLO = PLACE["weather_key"]
LONDON = "london-gb-2643743"


def test_the_keys_are_the_contracts():
    device_id = CONFIG["device_id"]
    assert config_store.key(device_id) == store_keys.config(device_id) == CONFIG["key"]
    assert config_store.PREFIX == store_keys.CONFIG_PREFIX
    assert pairing_key.key(PAIRING["device_id"]) == store_keys.pairing(PAIRING["device_id"]) == PAIRING["key"]
    assert frame_record.key(device_id) == store_keys.frame(device_id)
    assert render_jobs.queued_key(device_id) == store_keys.render_queued(device_id)
    assert (render_jobs.JOBS, render_jobs.SCREENS) == (store_keys.RENDER_JOBS, store_keys.RENDER_SCREENS)
    assert glow.energy_key(device_id) == store_keys.energy(device_id)
    assert music.player_key(device_id) == store_keys.spotify(device_id)
    assert music.top_key(device_id) == store_keys.spotify_top(device_id)
    assert developer.stored_key(device_id) == store_keys.developer(device_id)
    assert developer.pace_key(device_id) == pace.key("developer", device_id) == store_keys.pace("developer", device_id)
    assert parcel.parcels_key(device_id) == store_keys.parcels(device_id)
    assert parcel.status_key(device_id) == store_keys.parcels_status(device_id)
    url = "https://i.scdn.co/image/ab01300"
    assert album_art.art_key(url) == store_keys.album_art(url)
    assert device_keys.music(device_id) == [store_keys.spotify(device_id), store_keys.spotify_top(device_id)]
    assert loop.SUB_CHANGED == store_keys.SUB_CHANGED
    assert scripts.ENQUEUE.source == render_jobs._ENQUEUE


def test_the_ttls_are_the_contracts():
    assert render_jobs.QUEUED_TTL_MS == store_keys.RENDER_QUEUED_TTL_MS == 60_000
    assert pairing.CODE_TTL == timedelta(seconds=store_keys.PAIRING_TTL_S) == timedelta(minutes=10)
    assert glow.TTL_S == store_keys.ENERGY_TTL_S == 24 * 60 * 60
    assert music.PLAYER_TTL_S == store_keys.SPOTIFY_TTL_S == 3 * music.INTERVAL_S
    assert music.TOP_TTL_S == store_keys.SPOTIFY_TOP_TTL_S == 4 * music.TOP_INTERVAL_S
    assert album_art.ART_TTL_S == store_keys.ALBUM_ART_TTL_S == 5 * 60
    assert developer.TTL_S == store_keys.DEVELOPER_TTL_S == 15 * 60
    assert developer.PACE_TTL_S == store_keys.PACE_TTL_S == 60
    assert parcel.TTL_S == store_keys.PARCELS_TTL_S == 12 * 60 * 60
    assert parcel.STATUS_TTL_S == store_keys.PARCELS_STATUS_TTL_S == 24 * 60 * 60


def _add_example_devices(app) -> None:
    """Add the devices of config_store.json (paired, Glowmarkt and Parcel linked) and pairing_key.json (unpaired).

    And touchscreen.json's touchscreen, paired: it has no `settings`, so its row is what its `config` shows.
    """
    with app.app_context():
        now = datetime.now(UTC)
        settings = dict(CONFIG["settings"])
        glowmarkt = settings.pop("glowmarkt")
        paired = Device(device_id=CONFIG["device_id"], created_at=now, paired_at=now, owner="user-1")
        # The example shows energy: the screen's switch on, and its owner's account linked to Glowmarkt.
        paired.config = DeviceConfig(**settings, energy_enabled=True)
        unpaired = Device(device_id=PAIRING["device_id"], created_at=now)
        unpaired.config = DeviceConfig(**PAIRING["settings"])
        db.session.add_all([paired, unpaired])
        db.session.add(GlowmarktCredentials(user_id=account_of(CONFIG["device_id"]), username=glowmarkt["username"]))
        db.session.get_one(GlowmarktCredentials, account_of(CONFIG["device_id"])).password = glowmarkt["password"]
        # And parcels, from a Parcel API key on the same account.
        parcel_key = ParcelKey(user_id=account_of(CONFIG["device_id"]))
        parcel_key.key = "parcel-key"
        db.session.add(parcel_key)
        db.session.commit()
    _add_touchscreen(app, TOUCHSCREEN["device_id"])


def _add_touchscreen(
    app,
    device_id,
    *,
    kind="touchscreen",
    paired=True,
    postcode=TOUCHSCREEN["config"]["notice_board"]["postcode"],
    place=("Oslo", PLACE["value"]["name"], PLACE["value"]["country"], 3143244),
) -> None:
    """Add a screen showing `place` (name, label, country, Open-Meteo's id): by default, touchscreen.json's."""
    name, label, country, place_id = place
    with app.app_context():
        now = datetime.now(UTC)
        device = Device(
            device_id=device_id,
            type=kind,
            created_at=now,
            paired_at=now if paired else None,
            owner="user-1" if paired else None,
            display_secret_hash=f"hash-of-{device_id}",
        )
        device.config = DeviceConfig(
            weather_location=name,
            weather_place_name=name,
            weather_place_label=label,
            weather_place_id=place_id,
            weather_country_code=country,
            postcode=postcode,
            tfl_stop_ids=[stop["id"] for stop in TOUCHSCREEN["config"]["arrivals"]["stops"]],
        )
        db.session.add(device)
        db.session.commit()


def _write_subscriptions(app) -> None:
    with app.test_request_context():
        pages._write_subscriptions_to_redis()


def _places(fake_redis) -> dict[str, dict]:
    """Return the places in the store, by weather key."""
    return {
        key.removeprefix(store_keys.BACKGROUND_PLACE_PREFIX): json.loads(value)
        for key, value in fake_redis.strings.items()
        if key.startswith(store_keys.BACKGROUND_PLACE_PREFIX)
    }


def test_the_subscriptions_written_are_the_contracts_example(app, fake_redis):
    _add_example_devices(app)

    _write_subscriptions(app)

    assert SUBSCRIPTIONS["devices"] == ["config_store.json", "pairing_key.json", "touchscreen.json"]
    assert {key: sorted(members) for key, members in fake_redis.sets.items()} == {
        key: sorted(members) for key, members in SUBSCRIPTIONS["sets"].items()
    }
    assert set(fake_redis.sets) == {store_keys.subscription(source) for source in store_keys.SOURCES}
    assert fake_redis.published == [(SUBSCRIPTIONS["channel"], SUBSCRIPTIONS["message"])]


def test_the_background_place_written_is_the_contracts_example(app, fake_redis):
    _add_example_devices(app)

    _write_subscriptions(app)

    assert (PLACE["device"], PLACE["set"]) == ("touchscreen.json", store_keys.subscription(store_keys.BACKGROUND))
    assert store_keys.background_place(OSLO) == PLACE["key"]
    # Only the touchscreen's place: config_store.json's screen is paired and shows London, but is an e-paper one.
    assert [key for key in fake_redis.strings if key.startswith(store_keys.BACKGROUND_PLACE_PREFIX)] == [PLACE["key"]]
    assert json.loads(fake_redis.strings[PLACE["key"]]) == PLACE["value"]
    BackgroundPlace.model_validate_json(fake_redis.strings[PLACE["key"]])


def test_only_a_paired_touchscreens_place_is_a_background_place(app, fake_redis):
    london = ("London", "London, England, United Kingdom", "GB", 2643743)
    _add_touchscreen(app, "aaaaaaaaaaaa", paired=False)
    _add_touchscreen(app, "bbbbbbbbbbbb", kind="epaper", place=london, postcode="SE17 3LL")

    _write_subscriptions(app)

    assert fake_redis.sets[store_keys.subscription(store_keys.WEATHER)] == {OSLO, LONDON}
    assert store_keys.subscription(store_keys.BACKGROUND) not in fake_redis.sets
    assert _places(fake_redis) == {}


def test_touchscreens_showing_one_place_share_it_with_their_postcodes_sorted(app, fake_redis):
    _add_touchscreen(app, "aaaaaaaaaaaa", postcode="1367")
    _add_touchscreen(app, "bbbbbbbbbbbb", postcode="0150")
    _add_touchscreen(app, "cccccccccccc", postcode="")

    _write_subscriptions(app)

    assert fake_redis.sets[store_keys.subscription(store_keys.BACKGROUND)] == {OSLO}
    assert _places(fake_redis) == {OSLO: {**PLACE["value"], "postcodes": ["0150", "1367"]}}
    BackgroundPlace.model_validate_json(fake_redis.strings[store_keys.background_place(OSLO)])


def test_a_place_with_no_country_or_postcode_is_still_the_contracts(app, fake_redis):
    _add_touchscreen(app, "aaaaaaaaaaaa", postcode="", place=("Oslo", "Oslo, Norway", "no", 3143244))
    _add_touchscreen(app, "bbbbbbbbbbbb", postcode="", place=("Atlantis", None, None, None))

    _write_subscriptions(app)

    assert _places(fake_redis) == {
        OSLO: {"name": "Oslo, Norway", "country": "NO", "postcodes": []},
        "atlantis": {"name": "Atlantis", "country": None, "postcodes": []},
    }
    for value in fake_redis.strings.values():
        BackgroundPlace.model_validate_json(value)


def test_a_place_no_touchscreen_shows_any_more_is_deleted(app, fake_redis):
    _add_touchscreen(app, "aaaaaaaaaaaa")
    _add_touchscreen(app, "bbbbbbbbbbbb", postcode="SE17 3LL", place=("London", "London", "GB", 2643743))
    _write_subscriptions(app)
    assert set(_places(fake_redis)) == {OSLO, LONDON}

    # One moves to Bergen, and the other is unpaired.
    with app.app_context():
        moved = db.session.get_one(DeviceConfig, "aaaaaaaaaaaa")
        moved.weather_location = moved.weather_place_name = moved.weather_place_label = "Bergen"
        moved.weather_place_id = 3161732
        db.session.get_one(Device, "bbbbbbbbbbbb").paired_at = None
        db.session.commit()
    _write_subscriptions(app)

    assert fake_redis.sets[store_keys.subscription(store_keys.BACKGROUND)] == {"bergen-no-3161732"}
    assert _places(fake_redis) == {"bergen-no-3161732": {"name": "Bergen", "country": "NO", "postcodes": ["0150"]}}

    # And once it's gone too, so are the set and its place.
    with app.app_context():
        db.session.delete(db.session.get_one(Device, "aaaaaaaaaaaa"))
        db.session.commit()
    _write_subscriptions(app)

    assert store_keys.subscription(store_keys.BACKGROUND) not in fake_redis.sets
    assert _places(fake_redis) == {}


def test_a_store_that_cant_be_reached_logs_no_place_or_postcode(app, fake_redis, caplog):
    _add_touchscreen(app, "aaaaaaaaaaaa")
    fake_redis.fail = True

    _write_subscriptions(app)

    assert "Couldn't publish subscriptions to Redis (ConnectionError)" in caplog.text
    assert "0150" not in caplog.text
    assert "Oslo" not in caplog.text


def test_the_pace_a_provider_writes_is_read_as_a_hint(app):
    # As countdown's provider runner and the broker's developer provider write it: seconds, with the pace TTL.
    store = fakeredis.FakeRedis(decode_responses=True)
    app.extensions["redis"] = store
    device_id = CONFIG["device_id"]
    store.set(store_keys.pace(store_keys.DEVELOPER, device_id), 20, ex=store_keys.PACE_TTL_S)
    now = datetime.now(UTC)

    with app.app_context():
        hints = pace._stored_hints([pace.key(store_keys.DEVELOPER, device_id)], now)

    assert [hint.seconds for hint in hints] == [20]
    assert now < hints[0].until <= now + timedelta(seconds=store_keys.PACE_TTL_S)
