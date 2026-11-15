"""The settings pages embedded (?embed=1) in a device's own settings, e.g. a touchscreen's Settings tab.

An embedded page has no site navigation and no list of screens (base.html), only the page itself, and the page's
links to itself (its form's action, the redirect after a save or an unlink) keep ?embed=1.
"""

from typing import Any

from flask import Blueprint, has_request_context, request

bp = Blueprint("embed", __name__)

# The pages an embedded page's own forms and redirects lead to, which stay embedded.
EMBEDDED_ENDPOINTS = frozenset({"pages.device_config", "pages.display_unlink"})


def embedded() -> bool:
    """Whether the request asks for the page embedded (?embed=1)."""
    return has_request_context() and request.args.get("embed") == "1"


@bp.app_context_processor
def embed_mode() -> dict[str, bool]:
    """Tell every page whether it's embedded."""
    return {"embedded": embedded()}


@bp.app_url_defaults
def keep_embedded(endpoint: str, values: dict[str, Any]) -> None:
    """Make an embedded page's links to itself embedded too."""
    if endpoint in EMBEDDED_ENDPOINTS and "embed" not in values and embedded():
        values["embed"] = "1"
