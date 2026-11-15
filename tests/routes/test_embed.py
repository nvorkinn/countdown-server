"""Test the settings page embedded (?embed=1) in a device's own settings: no navigation, and it stays embedded."""

from tests.routes.test_touchscreens import _paired


def test_an_embedded_page_has_no_navigation_or_list_of_screens(app, client, paired_client):
    touchscreen = _paired(app, client)
    _, epaper, _ = paired_client

    for device_id in (touchscreen, epaper):
        page = client.get(f"/device/{device_id}").get_data(as_text=True)
        embedded = client.get(f"/device/{device_id}?embed=1").get_data(as_text=True)

        assert 'id="sidebar"' in page
        assert 'id="sidebar"' not in embedded
        assert 'id="menuButton"' not in embedded
        assert "+ Add a screen" not in embedded
        # The page itself is all there.
        assert 'id="settingsForm"' in embedded
        assert f'action="/device/{device_id}?embed=1"' in embedded


def test_an_embedded_page_stays_embedded_across_a_save(app, client):
    device_id = _paired(app, client)

    saved = client.post(f"/device/{device_id}?embed=1", data={"clock_format": "12h"})
    refused = client.post(f"/device/{device_id}?embed=1", data={"theme": "dusk"})

    assert saved.status_code == 302
    assert saved.headers["Location"] == f"/device/{device_id}?embed=1"
    assert refused.status_code == 400
    assert 'id="sidebar"' not in refused.get_data(as_text=True)
    # And a page that isn't embedded stays so.
    assert (
        client.post(f"/device/{device_id}", data={"clock_format": "24h"}).headers["Location"] == f"/device/{device_id}"
    )
