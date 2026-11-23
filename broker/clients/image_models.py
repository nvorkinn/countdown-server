"""The image model that makes the weather panel's backgrounds: OpenAI's.

It makes a picture from a prompt, or relights one it's given (the same view with other light and weather), and answers
its JPEG's bytes. A picture takes about a minute either way. A failure of any kind is an ImageModelError, with nothing
of the request in its text but the model's status: the key goes in a header, and the prompt can name the device's place.
"""

import base64
import os
from typing import Protocol

import requests

OPENAI_URL = "https://api.openai.com/v1/images"
# The 10.1" touchscreen's real pixels (16:10): it lays the page out at 1280x800 and draws it 1.5x, so a 1280x800 photo
# came out soft there. gpt-image-2 makes this size as asked, for about 40% more than 1280x800; smaller screens scale
# it down (the 1024x600 one crops it to cover).
OPENAI_SIZE = "1920x1200"
# A JPEG this compressed is about 250 KB at that size, which the screens load over Wi-Fi.
OPENAI_COMPRESSION = 85
# gpt-image-2 took 65 s for one at high quality, 2.6-sunburst about 35 s at medium: well inside this.
OPENAI_TIMEOUT_S = 300


class ImageModelError(Exception):
    """The model made no picture: it refused, failed, timed out or couldn't be reached."""


class ImageModel(Protocol):
    """Makes a background: from a prompt, or by relighting `reference` (a JPEG) as the prompt says."""

    name: str

    def make(self, prompt: str, reference: bytes | None = None) -> bytes:
        """Return the picture's JPEG. Raises ImageModelError."""
        ...


class OpenAIImages:
    """OpenAI's image API: generations for a new picture, edits to relight one."""

    def __init__(self, api_key: str, model: str, quality: str, session: requests.Session | None = None) -> None:
        """`model` is OPENAI_IMAGE_MODEL's (gpt-image-2), `quality` OPENAI_IMAGE_QUALITY's (low, medium or high)."""
        self.name = model
        self._quality = quality
        self._session = session or requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    def make(self, prompt: str, reference: bytes | None = None) -> bytes:
        """Return the picture's JPEG. Raises ImageModelError."""
        fields = {
            "model": self.name,
            "prompt": prompt,
            "size": OPENAI_SIZE,
            "quality": self._quality,
            "output_format": "jpeg",
            "output_compression": OPENAI_COMPRESSION,
            "n": 1,
        }
        try:
            if reference is None:
                response = self._session.post(f"{OPENAI_URL}/generations", json=fields, timeout=OPENAI_TIMEOUT_S)
            else:
                response = self._session.post(
                    f"{OPENAI_URL}/edits",
                    data={name: str(value) for name, value in fields.items()},
                    files={"image[]": ("reference.jpg", reference, "image/jpeg")},
                    timeout=OPENAI_TIMEOUT_S,
                )
        except requests.RequestException as error:
            msg = f"OpenAI couldn't be reached ({type(error).__name__})"
            raise ImageModelError(msg) from None
        if not response.ok:
            msg = f"OpenAI answered {response.status_code}: {_openai_error(response)}"
            raise ImageModelError(msg)
        try:
            return base64.b64decode(response.json()["data"][0]["b64_json"])
        except ValueError, KeyError, IndexError, TypeError:
            msg = "OpenAI's answer had no picture"
            raise ImageModelError(msg) from None


def _openai_error(response: requests.Response) -> str:
    """Return OpenAI's error code (moderation_blocked...), not its message, which can quote the prompt."""
    try:
        error = response.json()["error"]
        return str(error.get("code") or error.get("type") or "no code")
    except ValueError, KeyError, TypeError, AttributeError:
        return "no code"


# The settings (config/<environment>.env) and their defaults.
DEFAULT_OPENAI_MODEL = "gpt-image-2.6-sunburst"
DEFAULT_OPENAI_QUALITY = "medium"


def configured() -> list[ImageModel]:
    """Return the models to try, in order: OpenAI's, if broker.env has its key; none without."""
    models: list[ImageModel] = []
    if key := os.environ.get("OPENAI_API_KEY"):
        models.append(
            OpenAIImages(
                key,
                os.environ.get("OPENAI_IMAGE_MODEL") or DEFAULT_OPENAI_MODEL,
                os.environ.get("OPENAI_IMAGE_QUALITY") or DEFAULT_OPENAI_QUALITY,
            )
        )
    return models
