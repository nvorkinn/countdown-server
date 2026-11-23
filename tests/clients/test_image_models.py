"""The image model (broker/clients/image_models.py): OpenAI's API, faked with `responses`."""

import base64
import json

import pytest
import responses

from broker.clients import image_models
from broker.clients.image_models import OPENAI_URL, ImageModelError, OpenAIImages

JPEG = b"\xff\xd8\xff\xe0 a picture"


def _body(call) -> bytes:
    """Return a faked call's request body, which `responses` types loosely, as the bytes or text it is."""
    body = call.request.body
    assert isinstance(body, bytes | str)
    return body.encode() if isinstance(body, str) else body


def _openai_answer():
    return {"data": [{"b64_json": base64.b64encode(JPEG).decode()}]}


@responses.activate
def test_openai_makes_a_landscape_webp_with_the_key():
    call = responses.post(f"{OPENAI_URL}/generations", json=_openai_answer())

    picture = OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt")

    assert picture == JPEG
    sent = json.loads(_body(call.calls[0]))
    assert sent | {"prompt": "a prompt"} == sent
    assert (sent["model"], sent["size"], sent["quality"], sent["output_format"]) == (
        "gpt-image-2",
        "1920x1200",
        "high",
        "webp",
    )
    assert call.calls[0].request.headers["Authorization"] == "Bearer sk-test"


@responses.activate
def test_openai_relights_a_reference_with_an_edit():
    call = responses.post(f"{OPENAI_URL}/edits", json=_openai_answer())

    picture = OpenAIImages("sk-test", "gpt-image-2", "high").make("relight", JPEG)

    assert picture == JPEG
    body = _body(call.calls[0])
    assert b'name="image[]"' in body
    assert b"Content-Type: image/jpeg" in body
    assert JPEG in body


@responses.activate
def test_an_openai_refusal_names_its_code_not_the_prompt():
    responses.post(
        f"{OPENAI_URL}/generations",
        status=400,
        json={"error": {"code": "moderation_blocked", "message": "the prompt about Somewhere"}},
    )

    with pytest.raises(ImageModelError, match="400: moderation_blocked") as error:
        OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt about Somewhere")
    assert "Somewhere" not in str(error.value)


def test_the_configured_model_is_openais_when_it_has_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert image_models.configured() == []

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
    assert [model.name for model in image_models.configured()] == ["gpt-image-2.5-sunburst"]
