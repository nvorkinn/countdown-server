"""Test the Parcel API key's own page (GET and POST /account/parcel, the contract's connectParcelPage).

A screen whose owner's account has no key sends a phone here: where to find the key and a box to paste it into, and
nothing else of the account. A save is the Account page's, for every screen of the user's.
"""

import pytest

from broker.db import db
from broker.models import ParcelKey
from tests.conftest import account_of

PAGE = "/account/parcel"


def test_the_page_is_only_the_parcel_key_with_where_to_find_it(paired_client):
    client, _device_id, _ = paired_client

    response = client.get(PAGE)

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "<h1>Connect Parcel</h1>" in page
    assert 'href="https://web.parcelapp.net"' in page
    assert 'name="parcel_api_key"' in page
    assert 'id="parcelPaste"' in page
    assert "parcel_connect.js" in page
    # Nothing else of the account, and no sidebar of the user's screens.
    assert "glowmarkt" not in page.lower()
    assert "github_token" not in page
    assert 'class="sidebar"' not in page
    assert "already saved" not in page


def test_a_pasted_key_is_saved_for_the_account_and_the_page_says_so(app, paired_client, fake_redis):
    client, device_id, _ = paired_client
    client.post("/device", data={"interval": "15", "parcels_enabled": "on"})

    response = client.post(PAGE, data={"parcel_api_key": " parcel-key-abc\n"})

    assert response.status_code == 302
    assert response.headers["Location"] == f"{PAGE}?saved=1"
    with app.app_context():
        row = db.session.get_one(ParcelKey, account_of(device_id))
        assert row.key == "parcel-key-abc"
        assert row.key_encrypted != "parcel-key-abc"
    # The screen's config now has its parcels on, and the provider is told to read them.
    assert device_id in fake_redis.sets["v1:sub:parcel"]
    saved = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "<strong>Saved.</strong>" in saved
    # Done: the steps and the box are gone, with a way back to them.
    assert 'name="parcel_api_key"' not in saved
    assert f'href="{PAGE}"' in saved
    assert "parcel-key-abc" not in saved


def test_a_blank_save_keeps_the_saved_key_and_asks_for_one(app, paired_client):
    client, device_id, _ = paired_client
    client.post(PAGE, data={"parcel_api_key": "parcel-key-abc"})

    response = client.post(PAGE, data={"parcel_api_key": "  "})

    assert response.headers["Location"] == f"{PAGE}?empty=1"
    with app.app_context():
        assert db.session.get_one(ParcelKey, account_of(device_id)).key == "parcel-key-abc"
    assert "<strong>Nothing to save.</strong>" in client.get(response.headers["Location"]).get_data(as_text=True)


def test_the_page_doesnt_say_saved_to_an_account_without_a_key(paired_client):
    client, _device_id, _ = paired_client

    assert "<strong>Saved.</strong>" not in client.get(f"{PAGE}?saved=1").get_data(as_text=True)


@pytest.mark.parametrize(
    ("status", "says"),
    [
        (None, "A key is already saved."),
        ("ok", "A key is already saved."),
        ("invalid_key", "Parcel refused the saved key."),
    ],
)
def test_the_page_says_when_a_key_is_saved_or_refused_and_never_shows_it(paired_client, fake_redis, status, says):
    client, device_id, _ = paired_client
    client.post(PAGE, data={"parcel_api_key": "parcel-key-abc"})
    if status:
        fake_redis.strings[f"v1:parcels-status:{device_id}"] = status

    page = client.get(PAGE).get_data(as_text=True)

    assert f"<strong>{says}</strong>" in page
    assert "parcel-key-abc" not in page


def test_the_page_needs_the_login(anonymous_client):
    assert anonymous_client.get(PAGE).status_code == 401
    assert anonymous_client.post(PAGE, data={"parcel_api_key": "parcel-key-abc"}).status_code == 401
