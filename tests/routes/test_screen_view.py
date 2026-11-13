import json
import struct
import zlib
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from broker.db import db
from broker.models import DeviceConfig
from broker.services import config_store, frame_image, frame_record, pace, render_jobs
from broker.services.frame_record import FRAME_BYTES

SMALL, HD = (800, 480), (1872, 1404)


def _frame_with_black_pixel(x: int, y: int) -> bytes:
    """Return a white frame in the panel's own layout (800x480, set bit = black) with one black pixel."""
    frame = bytearray(FRAME_BYTES)
    frame[(y * 800 + x) // 8] |= 0x80 >> (x % 8)
    return bytes(frame)


def _decode(png: bytes) -> tuple[int, int, list[bytes]]:
    """Return (width, height, rows) of an 8-bit greyscale PNG with unfiltered rows, as to_png writes them."""
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    pos, chunks = 8, {}
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos : pos + 4])
        kind, data = png[pos + 4 : pos + 8], png[pos + 8 : pos + 8 + length]
        assert struct.unpack(">I", png[pos + 8 + length : pos + 12 + length])[0] == zlib.crc32(kind + data)
        chunks[kind] = chunks.get(kind, b"") + data
        pos += 12 + length
    width, height, depth, colour, *_ = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    assert (depth, colour) == (8, 0)
    raw = zlib.decompress(chunks[b"IDAT"])
    rows = [raw[r * (width + 1) : (r + 1) * (width + 1)] for r in range(height)]
    assert all(row[0] == 0 for row in rows)
    return width, height, [row[1:] for row in rows]


def _black_pixels(png: bytes) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    width, height, rows = _decode(png)
    return (width, height), [(x, y) for y, row in enumerate(rows) for x, value in enumerate(row) if value == 0]


def test_a_landscape_frame_is_drawn_as_is():
    size, black = _black_pixels(frame_image.to_png(_frame_with_black_pixel(10, 3), SMALL, portrait=False))

    assert size == (800, 480)
    assert black == [(10, 3)]


def test_a_portrait_frame_is_turned_back_a_quarter_clockwise():
    # The render turned the portrait picture a quarter anticlockwise: portrait (x, y) is at landscape (y, 479 - x).
    portrait_x, portrait_y = 10, 3
    frame = _frame_with_black_pixel(portrait_y, 479 - portrait_x)
    size, black = _black_pixels(frame_image.to_png(frame, SMALL, portrait=True))

    assert size == (480, 800)
    assert black == [(portrait_x, portrait_y)]


PNG_FRAME = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


def _make_png_screen(app, device_id, width, height, orientation="vertical"):
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.width, config.height, config.format, config.orientation = width, height, "png", orientation
        db.session.commit()


def test_a_png_frame_is_served_as_it_is_in_portrait_or_landscape():
    # The page turns a portrait one back (the frame-portrait style); the broker never decodes it.
    assert frame_image.preview(PNG_FRAME, "png", (1404, 1872), portrait=True) == PNG_FRAME
    assert frame_image.preview(PNG_FRAME, "png", (1872, 1404), portrait=False) == PNG_FRAME


def test_a_mono_frame_is_previewed_as_before():
    frame = _frame_with_black_pixel(10, 3)

    assert frame_image.preview(frame, "mono-1bpp-packed", (480, 800), portrait=True) == frame_image.to_png(
        frame, SMALL, portrait=True
    )


def _hd_frame_with_black_pixel(x: int, y: int) -> bytes:
    """Return a white HD frame (1872x1404 landscape, set bit = black) with one black pixel."""
    frame = bytearray(1872 * 1404 // 8)
    frame[(y * 1872 + x) // 8] |= 0x80 >> (x % 8)
    return bytes(frame)


def test_an_hd_frame_is_read_at_its_own_width():
    size, black = _black_pixels(
        frame_image.preview(_hd_frame_with_black_pixel(1000, 3), "mono-1bpp-packed", HD, portrait=False)
    )

    assert size == (1872, 1404)
    assert black == [(1000, 3)]


def test_an_hd_portrait_frame_is_turned_back_at_its_own_size():
    # Portrait (x, y) is at landscape (y, 1403 - x), whichever way round the screen gave its size.
    portrait_x, portrait_y = 10, 1500
    frame = _hd_frame_with_black_pixel(portrait_y, 1403 - portrait_x)
    size, black = _black_pixels(frame_image.preview(frame, "mono-1bpp-packed", (1404, 1872), portrait=True))

    assert size == (1404, 1872)
    assert black == [(portrait_x, portrait_y)]


def test_a_frame_not_the_screens_size_is_refused_rather_than_shown_as_noise():
    with pytest.raises(ValueError, match="1872x1404"):
        frame_image.preview(_frame_with_black_pixel(10, 3), "mono-1bpp-packed", HD, portrait=True)


def test_a_portrait_png_screen_turns_its_picture_on_the_page(app, paired_client, draw):
    client, device_id, _secret = paired_client
    _make_png_screen(app, device_id, 1872, 1404)
    draw(device_id, PNG_FRAME)

    screen = client.get(f"/device/{device_id}/screen").get_data(as_text=True)
    settings = client.get(f"/device/{device_id}").get_data(as_text=True)
    picture = client.get(f"/device/{device_id}/screen/frame.png")

    for html in (screen, settings):
        assert 'class="frame frame-portrait" style="--frame-w: 1404; --frame-h: 1872"' in html
        assert 'width="1872" height="1404"' in html  # the picture as it comes: landscape
    assert picture.status_code == 200
    assert picture.data == PNG_FRAME


def test_a_landscape_png_screen_shows_its_picture_as_it_is(app, paired_client, draw):
    client, device_id, _secret = paired_client
    _make_png_screen(app, device_id, 1872, 1404, orientation="horizontal")
    draw(device_id, PNG_FRAME)

    html = client.get(f"/device/{device_id}/screen").get_data(as_text=True)

    assert 'class="frame" style="--frame-w: 1872; --frame-h: 1404"' in html
    assert 'class="frame frame-portrait"' not in html
    assert "<li>Horizontal</li>" in client.get(f"/device/{device_id}").get_data(as_text=True)


def test_a_mono_screen_is_never_turned_on_the_page(paired_client, draw):
    client, device_id, _secret = paired_client
    draw(device_id, bytes(FRAME_BYTES))

    html = client.get(f"/device/{device_id}/screen").get_data(as_text=True)

    assert 'class="frame" style="--frame-w: 480; --frame-h: 800"' in html


def test_screen_page_shows_the_picture_and_polls_at_the_screens_interval(app, paired_client, draw):
    client, device_id, _secret = paired_client
    draw(device_id, bytes(FRAME_BYTES))

    response = client.get(f"/device/{device_id}/screen")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'src="/device/{device_id}/screen/frame.png"' in html
    assert "checked when the screen checks: every 30 seconds." in html  # twice a render, as the screen polls
    assert "/static/frame_poll.js" in html
    assert 'width="480" height="800"' in html


def test_screen_page_polls_as_fast_as_the_screen_while_a_provider_asks(app, paired_client, draw):
    client, device_id, _secret = paired_client
    draw(device_id, bytes(FRAME_BYTES))
    with app.app_context():
        db.session.get_one(DeviceConfig, device_id).developer_enabled = True
        db.session.commit()
    app.extensions["redis"] = store = fakeredis.FakeRedis(decode_responses=True)
    store.set(f"v1:pace:developer:{device_id}", "20", ex=60)

    html = client.get(f"/device/{device_id}/screen").get_data(as_text=True)

    assert "checked when the screen checks: every 10 seconds." in html


def test_screen_page_before_the_first_frame_says_so(paired_client):
    client, device_id, _ = paired_client

    html = client.get(f"/device/{device_id}/screen").get_data(as_text=True)

    assert "Nothing drawn yet" in html
    assert client.get(f"/device/{device_id}/screen/frame.png").status_code == 404


def test_settings_page_links_to_the_screen(paired_client):
    client, device_id, _ = paired_client

    html = client.get(f"/device/{device_id}").get_data(as_text=True)

    assert f'href="/device/{device_id}/screen"' in html


def test_settings_page_header_shows_the_screens_picture(paired_client, draw):
    client, device_id, _ = paired_client
    draw(device_id, bytes(FRAME_BYTES))

    html = client.get(f"/device/{device_id}").get_data(as_text=True)

    picture = f'<img class="frame-picture" src="/device/{device_id}/screen/frame.png" alt="" width="480" height="800">'
    assert picture in html
    assert "No picture yet" not in html


def test_settings_page_header_says_when_the_picture_was_drawn(paired_client, draw):
    client, device_id, _ = paired_client
    draw(device_id, bytes(FRAME_BYTES), at=datetime.now(UTC) - timedelta(minutes=7))

    html = client.get(f"/device/{device_id}").get_data(as_text=True)

    assert '<figcaption id="previewDrawn">Drawn 7 min ago</figcaption>' in html


def test_settings_page_header_before_the_first_frame_says_so(paired_client):
    client, device_id, _ = paired_client

    html = client.get(f"/device/{device_id}").get_data(as_text=True)

    assert "No picture yet" in html
    # No picture to show, only where the page asks for one until it's drawn.
    assert 'src="' not in html.split('id="preview"')[1].split("</figure>")[0]
    assert f'data-frame-url="/device/{device_id}/screen/frame.png"' in html
    assert '<figcaption id="previewDrawn" hidden></figcaption>' in html


def test_settings_page_with_a_picture_keeps_asking_for_the_latest(paired_client, draw):
    # The picture loaded with the page is the one from before a save: its render lands a moment later.
    client, device_id, _ = paired_client
    draw(device_id, bytes(FRAME_BYTES))

    html = client.get(f"/device/{device_id}").get_data(as_text=True)

    assert f'data-frame-url="/device/{device_id}/screen/frame.png"' in html
    # With the ETag of the picture it loaded, so the first ask doesn't download it again.
    etag = client.get(f"/device/{device_id}/screen/frame.png").headers["ETag"]
    assert f"data-frame-etag='{etag}'" in html
    assert "/static/frame_poll.js" in html


def test_the_picture_says_when_to_ask_again_as_the_screens_own_poll_does(paired_client, draw, monkeypatch):
    client, device_id, secret = paired_client
    draw(device_id, bytes(FRAME_BYTES))
    # 12:00:10: the screen's next wake is at :33.
    monkeypatch.setattr(pace, "clock", lambda: datetime(2026, 11, 13, 12, 0, 10, tzinfo=UTC))

    picture = client.get(f"/device/{device_id}/screen/frame.png")
    poll = client.post("/api/frame", headers={"Authorization": f"Bearer {secret}"})

    assert picture.headers["Retry-After"] == "23"
    assert poll.headers["Retry-After"] == "23"


def test_an_unchanged_picture_is_a_304_that_says_when_to_ask_again_without_reading_the_frame(
    paired_client, draw, monkeypatch
):
    client, device_id, _ = paired_client
    draw(device_id, bytes(FRAME_BYTES))
    etag = client.get(f"/device/{device_id}/screen/frame.png").headers["ETag"]
    monkeypatch.setattr(frame_record, "frame_of", lambda *_: pytest.fail("the frame was read"))
    monkeypatch.setattr(frame_record, "read", lambda *_: pytest.fail("the frame was read"))

    again = client.get(f"/device/{device_id}/screen/frame.png", headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert int(again.headers["Retry-After"]) >= pace.MIN_SLEEP_S
    assert "X-Rendered-At" in again.headers


def test_a_picture_there_isnt_yet_is_asked_for_again_soon(paired_client):
    client, device_id, _ = paired_client

    missing = client.get(f"/device/{device_id}/screen/frame.png")

    assert missing.status_code == 404
    assert 0 < int(missing.headers["Retry-After"]) <= 10


def test_turning_the_screen_forgets_the_picture_drawn_the_other_way(paired_client, draw):
    # It would be shown on its side until the pool's next render, which the save queues.
    client, device_id, _ = paired_client
    draw(device_id, bytes(FRAME_BYTES))

    assert client.post(f"/device/{device_id}", data={"orientation": "horizontal"}).status_code == 302

    assert client.get(f"/device/{device_id}/screen/frame.png").status_code == 404
    assert "No picture yet" in client.get(f"/device/{device_id}").get_data(as_text=True)


def test_a_turned_screens_picture_is_forgotten_after_its_config_is_stored_and_before_its_render(
    app, paired_client, draw, fake_redis, monkeypatch
):
    # The pool writes no frame drawn from a config since replaced, so once the new config is stored nothing can put
    # the old picture back; and the render queued last draws again whatever was drawn in between.
    client, device_id, _ = paired_client
    draw(device_id, bytes(FRAME_BYTES))
    events = []
    forget, enqueue = frame_record.forget, render_jobs.enqueue

    def forget_and_note(forgotten):
        stored = json.loads(fake_redis.get(config_store.key(forgotten)))
        events.append(("forget", stored["screen"]["orientation"]))
        forget(forgotten)

    def enqueue_and_note(redis, device_ids):
        events.append(("render", list(device_ids)))
        return enqueue(redis, device_ids)

    monkeypatch.setattr(frame_record, "forget", forget_and_note)
    monkeypatch.setattr(render_jobs, "enqueue", enqueue_and_note)

    assert client.post(f"/device/{device_id}", data={"orientation": "horizontal"}).status_code == 302

    assert events[-2:] == [("forget", "horizontal"), ("render", [device_id])]


def test_a_save_that_doesnt_turn_the_screen_keeps_its_picture(paired_client, draw):
    client, device_id, _ = paired_client
    draw(device_id, bytes(FRAME_BYTES))

    assert client.post(f"/device/{device_id}", data={"orientation": "vertical", "language": "nb"}).status_code == 302

    assert client.get(f"/device/{device_id}/screen/frame.png").status_code == 200


def test_settings_page_header_lists_the_saved_settings(app, paired_client):
    client, device_id, _ = paired_client
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.style, config.language, config.clock_format = "card", "nb", "12h"
        db.session.commit()

    html = client.get(f"/device/{device_id}").get_data(as_text=True)

    for chip in ("Card", "Vertical", "Norsk", "0 stops", "12-hour"):
        assert f"<li>{chip}</li>" in html


def test_frame_png_is_the_frame_in_portrait_and_conditional(paired_client, draw):
    client, device_id, _secret = paired_client
    draw(device_id, _frame_with_black_pixel(3, 479 - 10))

    response = client.get(f"/device/{device_id}/screen/frame.png")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.headers["Cache-Control"] == "private, no-cache"
    assert "X-Rendered-At" in response.headers
    assert _black_pixels(response.data) == ((480, 800), [(10, 3)])

    again = client.get(f"/device/{device_id}/screen/frame.png", headers={"If-None-Match": response.headers["ETag"]})
    assert again.status_code == 304
    assert again.data == b""
    assert "X-Rendered-At" in again.headers


def test_frame_png_follows_a_landscape_screen(app, paired_client, draw):
    client, device_id, _secret = paired_client
    draw(device_id, _frame_with_black_pixel(10, 3))
    portrait = client.get(f"/device/{device_id}/screen/frame.png")
    with app.app_context():
        db.session.get_one(DeviceConfig, device_id).orientation = "horizontal"
        db.session.commit()

    response = client.get(f"/device/{device_id}/screen/frame.png", headers={"If-None-Match": portrait.headers["ETag"]})

    assert response.status_code == 200
    assert _black_pixels(response.data) == ((800, 480), [(10, 3)])


def test_the_screen_is_only_its_owners(app, paired_client, anonymous_client, draw):
    _client, device_id, _secret = paired_client
    draw(device_id, bytes(FRAME_BYTES))
    stranger = app.test_client()
    stranger.environ_base["HTTP_REMOTE_SUB"] = "someone-else"

    for path in (f"/device/{device_id}/screen", f"/device/{device_id}/screen/frame.png"):
        assert stranger.get(path).status_code == 404
        assert anonymous_client.get(path).status_code == 401


def test_an_hd_mono_screens_preview_is_its_own_size(app, paired_client, draw):
    client, device_id, _secret = paired_client
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.width, config.height, config.orientation = 1872, 1404, "vertical"
        db.session.commit()
    draw(device_id, _hd_frame_with_black_pixel(1500, 1403 - 10))

    response = client.get(f"/device/{device_id}/screen/frame.png")

    assert response.status_code == 200
    size, black = _black_pixels(response.data)
    assert size == (1404, 1872)
    assert black == [(10, 1500)]


def test_a_frame_drawn_for_another_size_is_no_picture_yet(app, paired_client, draw):
    client, device_id, _secret = paired_client
    with app.app_context():
        config = db.session.get_one(DeviceConfig, device_id)
        config.width, config.height = 1872, 1404
        db.session.commit()
    draw(device_id, _frame_with_black_pixel(10, 3))

    response = client.get(f"/device/{device_id}/screen/frame.png")

    assert response.status_code == 404
    assert "Retry-After" in response.headers
