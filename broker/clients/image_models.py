"""The image models that make the weather panel's backgrounds: OpenAI's, and Black Forest Labs' FLUX as the fallback.

Each makes a picture from a prompt, or relights one it's given (the same view with other light and weather), and answers
its JPEG's bytes. A picture takes about a minute either way. A failure of any kind is an ImageModelError, with nothing
of the request in its text but the model's status: the key goes in a header, and the prompt can name the device's place.
"""

import base64
import os
import time
from typing import TYPE_CHECKING, Protocol

import requests

if TYPE_CHECKING:
    from collections.abc import Callable

OPENAI_URL = "https://api.openai.com/v1/images"
# Landscape, the widest OpenAI makes; the screens are 1024x600 and 1280x800 and crop it to cover.
OPENAI_SIZE = "1536x1024"
# A JPEG this compressed is a few hundred KB at that size, which the screens load over Wi-Fi.
OPENAI_COMPRESSION = 85
# gpt-image-2 took 65 s for one at high quality, 2.6-sunburst about 35 s at medium: well inside this.
OPENAI_TIMEOUT_S = 300

FLUX_URL = "https://api.bfl.ai/v1/flux-2-pro"
FLUX_WIDTH, FLUX_HEIGHT = 1920, 1200
FLUX_POLL_S = 1.0
FLUX_TIMEOUT_S = 300
REQUEST_TIMEOUT_S = 30


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


class FluxImages:
    """Black Forest Labs' FLUX 2 pro: start a job, poll it, then fetch the picture it signs a URL for."""

    name = "flux-2-pro"

    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`sleep` and `clock` are parameters for the tests."""
        self._session = session or requests.Session()
        self._session.headers.update({"x-key": api_key, "accept": "application/json"})
        self._sleep = sleep
        self._clock = clock

    def make(self, prompt: str, reference: bytes | None = None) -> bytes:
        """Return the picture's JPEG. Raises ImageModelError."""
        body: dict[str, str | int] = {
            "prompt": prompt,
            "width": FLUX_WIDTH,
            "height": FLUX_HEIGHT,
            "output_format": "jpeg",
        }
        if reference is not None:
            body["input_image"] = base64.b64encode(reference).decode()
        try:
            start = self._session.post(FLUX_URL, json=body, timeout=REQUEST_TIMEOUT_S)
            if not start.ok:
                msg = f"FLUX answered {start.status_code}"
                raise ImageModelError(msg)
            polling_url = start.json()["polling_url"]
            deadline = self._clock() + FLUX_TIMEOUT_S
            while self._clock() < deadline:
                self._sleep(FLUX_POLL_S)
                poll = self._session.get(polling_url, timeout=REQUEST_TIMEOUT_S).json()
                status = poll.get("status", "")
                if status == "Ready":
                    picture = requests.get(poll["result"]["sample"], timeout=REQUEST_TIMEOUT_S)
                    if not picture.ok:
                        msg = f"FLUX's picture answered {picture.status_code}"
                        raise ImageModelError(msg)
                    return picture.content
                if status in {"Error", "Failed"} or "Moderated" in status:
                    msg = f"FLUX said {status}"
                    raise ImageModelError(msg)
        except requests.RequestException as error:
            msg = f"FLUX couldn't be reached ({type(error).__name__})"
            raise ImageModelError(msg) from None
        except ValueError, KeyError, TypeError:
            msg = "FLUX's answer wasn't what it should be"
            raise ImageModelError(msg) from None
        msg = "FLUX timed out"
        raise ImageModelError(msg)


# The settings (config/<environment>.env) and their defaults.
DEFAULT_OPENAI_MODEL = "gpt-image-2.6-sunburst"
DEFAULT_OPENAI_QUALITY = "medium"


def configured() -> list[ImageModel]:
    """Return the models to try, in order, for the keys broker.env has: OpenAI's, then FLUX. None without either key."""
    models: list[ImageModel] = []
    if key := os.environ.get("OPENAI_API_KEY"):
        models.append(
            OpenAIImages(
                key,
                os.environ.get("OPENAI_IMAGE_MODEL") or DEFAULT_OPENAI_MODEL,
                os.environ.get("OPENAI_IMAGE_QUALITY") or DEFAULT_OPENAI_QUALITY,
            )
        )
    if key := os.environ.get("BFL_API_KEY"):
        models.append(FluxImages(key))
    return models
