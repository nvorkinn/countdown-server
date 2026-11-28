"""Test the look of the pages a touchscreen's setup sends its owner's phone through, and of no other page.

The pairing page for a touchscreen's token, the wait between steps, the Spotify sign-in's answer, the Soloist key and
"ready" look like the setup the touchscreen shows beside them (templates/touchscreen_base.html). The same pairing page
for an e-paper screen, and the rest of the settings site, look as they always have (templates/base.html).
"""

import pytest

from tests.routes.test_pairing_token import SCREEN_SECRET, token_for_secret
from tests.routes.test_touchscreen_soloist import KEY, PAGE_URL, _code, _key, _paste
from tests.routes.test_touchscreen_spotify_sign_in import CALLBACK_URL
from tests.routes.test_touchscreens import SECRET, _paired, _register

STYLESHEET = "/static/touchscreen_setup.css"
# Only base.html has the settings site's logo and its webfonts.
SITE_MARKS = ("countdown-pill.svg", "fonts.googleapis.com")


def _is_setup_look(html):
    return STYLESHEET in html and not any(mark in html for mark in SITE_MARKS)


def _is_site_look(html):
    return STYLESHEET not in html and all(mark in html for mark in SITE_MARKS)


def _html(response):
    assert response.status_code == 200
    return response.get_data(as_text=True)


# --- the pairing page -----------------------------------------------------------------------------


def test_the_pairing_page_for_a_registered_touchscreens_token_looks_like_its_setup(client):
    assert _register(client).status_code == 201

    html = _html(client.get(f"/pair/add?t={token_for_secret(SECRET)}"))

    assert _is_setup_look(html)
    # The same page underneath: its words, its script and its fallback form.
    assert "Connecting to your device…" in html
    assert "location.hash" in html
    assert '+ "/setup"' in html
    assert 'action="/pair/add"' in html
    assert 'name="code"' in html


def test_the_pairing_page_for_an_e_paper_screens_token_looks_as_it_did(client, register_device):
    register_device(SCREEN_SECRET)

    html = _html(client.get(f"/pair/add?t={token_for_secret()}"))

    assert _is_site_look(html)
    assert "location.hash" in html


@pytest.mark.parametrize("query", ["", f"?t={token_for_secret()}", "?t=not-a-token", "?code=ABC234"])
def test_the_pairing_page_with_no_registered_touchscreen_to_go_by_looks_as_it_did(client, query):
    assert _is_site_look(_html(client.get(f"/pair/add{query}")))


def test_a_wrong_code_is_refused_on_the_settings_sites_own_page(client):
    assert _register(client).status_code == 201

    html = _html(client.post("/pair/add", data={"code": "NOPE12"}))

    assert _is_site_look(html)
    assert "That code is invalid or has expired." in html


# --- the pages after it ---------------------------------------------------------------------------


def test_the_wait_between_steps_looks_like_the_setup(app, client):
    _paired(app, client)

    html = _html(client.get("/device/setup"))

    assert _is_setup_look(html)
    assert "One moment…" in html


def test_the_ready_page_looks_like_the_setup(app, client):
    _paired(app, client)

    html = _html(client.get("/pair/done"))

    assert _is_setup_look(html)
    assert "Your screen is ready." in html
    assert 'class="mark"' in html


def test_the_spotify_sign_ins_answer_looks_like_the_setup(anonymous_client):
    spent = _html(anonymous_client.get("/spotify/sign-in", query_string={"id": "no-such-sign-in"}))
    assert _is_setup_look(spent)
    assert "This code has expired." in spent

    assert _is_setup_look(_html(anonymous_client.get(CALLBACK_URL, query_string={"state": "none", "code": "x"})))


def test_the_soloist_key_page_looks_like_the_setup_in_each_of_its_states(app, client, anonymous_client):
    _paired(app, client)
    _paste(anonymous_client, _code(client))  # the touchscreen's first key, which ends its setup
    code = _code(client, "someone-else")

    asked = _html(anonymous_client.get(PAGE_URL, query_string={"code": code}))
    assert _is_setup_look(asked)
    assert '<label for="api_key">API key</label>' in asked
    assert f'<input type="hidden" name="code" value="{code}">' in asked

    assert _is_setup_look(_html(_paste(anonymous_client, code, "")))  # asked again

    saved = _html(_paste(anonymous_client, code))
    assert _is_setup_look(saved)
    assert "Your key is saved" in saved
    assert 'class="mark"' in saved

    spent = _html(anonymous_client.get(PAGE_URL, query_string={"code": code}))
    assert _is_setup_look(spent)
    assert 'class="mark is-problem"' in spent


def test_the_setup_pages_name_no_app(app, client, anonymous_client):
    _paired(app, client)
    pages = [
        client.get(f"/pair/add?t={token_for_secret(SECRET)}"),
        client.get("/device/setup"),
        client.get("/pair/done"),
        anonymous_client.get("/spotify/sign-in"),
        anonymous_client.get(PAGE_URL, query_string={"code": _code(client)}),
    ]

    for page in pages:
        assert "max mix" not in _html(page).lower()


# --- the Soloist key's Paste button ---------------------------------------------------------------


def test_the_soloist_key_page_has_a_paste_button_that_cannot_send_the_form(app, client, anonymous_client):
    _paired(app, client)

    html = _html(anonymous_client.get(PAGE_URL, query_string={"code": _code(client)}))

    # Hidden until its script finds a clipboard to read, so the page is whole without JavaScript.
    assert '<button type="button" class="button" id="soloistPaste" hidden>Paste</button>' in html
    # Under the box, over Save key, which it saves through.
    assert html.index('id="api_key"') < html.index('id="soloistPaste"') < html.index('id="soloistSave"')
    assert 'id="soloistPasteProblem" role="status" hidden' in html
    assert '<script src="/static/soloist_paste.js"></script>' in html


def test_a_key_is_saved_without_the_space_around_it(app, client, anonymous_client):
    _paired(app, client)

    assert _paste(anonymous_client, _code(client), f"  {KEY} \r\n").status_code == 303

    assert _key(client).get_json()["api_key"] == KEY


# --- every other page -----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/about", "/device", "/account"])
def test_the_settings_sites_own_pages_look_as_they_did(app, client, path):
    _paired(app, client)

    assert STYLESHEET not in _html(client.get(path))


def test_the_sign_up_page_looks_as_it_did(anonymous_client):
    assert _is_site_look(_html(anonymous_client.get(f"/pair?t={token_for_secret(SECRET)}")))
