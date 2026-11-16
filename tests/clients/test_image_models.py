"""The image models (broker/clients/image_models.py): OpenAI's and FLUX's APIs, faked with `responses`."""

import base64
import json

import pytest
import responses

from broker.clients import image_models
from broker.clients.image_models import FLUX_URL, OPENAI_URL, FluxImages, ImageModelError, OpenAIImages

JPEG = b"\xff\xd8\xff\xe0 a picture"


def _openai_answer():
    return {"data": [{"b64_json": base64.b64encode(JPEG).decode()}]}


@responses.activate
def test_openai_makes_a_landscape_jpeg_with_the_key():
    call = responses.post(f"{OPENAI_URL}/generations", json=_openai_answer())

    picture = OpenAIImages("sk-test", "gpt-image-2", "high").make("a prompt")

    assert picture == JPEG
    sent = json.loads(call.calls[0].request.body)
    assert sent | {"prompt": "a prompt"} == sent
    assert (sent["model"], sent["size"], sent["quality"], sent["output_format"]) == (
        "gpt-image-2",
        "1536x1024",
        "high",
        "jpeg",
    )
    assert call.calls[0].request.headers["Authorization"] == "Bearer sk-test"


@responses.activate
def test_openai_relights_a_reference_with_an_edit():
    call = responses.post(f"{OPENAI_URL}/edits", json=_openai_answer())

    picture = OpenAIImages("sk-test", "gpt-image-2", "high").make("relight", JPEG)

    assert picture == JPEG
    body = call.calls[0].request.body
    assert b'name="image[]"' in body
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


@responses.activate
def test_flux_starts_a_job_polls_it_and_fetches_the_picture():
    start = responses.post(FLUX_URL, json={"id": "1", "polling_url": "https://api.bfl.ai/v1/get_result?id=1"})
    responses.get("https://api.bfl.ai/v1/get_result", json={"status": "Pending"})
    responses.get(
        "https://api.bfl.ai/v1/get_result", json={"status": "Ready", "result": {"sample": "https://cdn.bfl.ai/p.jpg"}}
    )
    responses.get("https://cdn.bfl.ai/p.jpg", body=JPEG)

    picture = FluxImages("bfl-test", sleep=lambda _s: None).make("a prompt", JPEG)

    assert picture == JPEG
    sent = json.loads(start.calls[0].request.body)
    assert sent["input_image"] == base64.b64encode(JPEG).decode()
    assert start.calls[0].request.headers["x-key"] == "bfl-test"


@responses.activate
def test_a_moderated_flux_job_is_an_error():
    responses.post(FLUX_URL, json={"polling_url": "https://api.bfl.ai/v1/get_result?id=1"})
    responses.get("https://api.bfl.ai/v1/get_result", json={"status": "Request Moderated"})

    with pytest.raises(ImageModelError, match="Moderated"):
        FluxImages("bfl-test", sleep=lambda _s: None).make("a prompt")


def test_flux_gives_up_after_its_timeout():
    times = iter([0, 0, 1000])
    flux = FluxImages("bfl-test", sleep=lambda _s: None, clock=lambda: next(times))
    with responses.RequestsMock() as mock:
        mock.post(FLUX_URL, json={"polling_url": "https://api.bfl.ai/v1/get_result?id=1"})
        mock.get("https://api.bfl.ai/v1/get_result", json={"status": "Pending"})
        with pytest.raises(ImageModelError, match="timed out"):
            flux.make("a prompt")


def test_the_configured_models_are_openais_then_flux_for_the_keys_there_are(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BFL_API_KEY", raising=False)
    assert image_models.configured() == []

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_IMAGE_MODEL", "gpt-image-2.5-sunburst")
    monkeypatch.setenv("BFL_API_KEY", "bfl-test")
    assert [model.name for model in image_models.configured()] == ["gpt-image-2.5-sunburst", "flux-2-pro"]
