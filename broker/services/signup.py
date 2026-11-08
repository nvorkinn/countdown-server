"""Signing a new owner up: check the pairing code, make their Pocket ID account, and reserve the screen for them."""

from http import HTTPStatus
from typing import TYPE_CHECKING

from flask import current_app

from broker.clients.pocket_id import PocketIdError, UserExistsError
from broker.services import pairing

if TYPE_CHECKING:
    from broker.clients.pocket_id import PocketIdClient

MAX_NAME = 100
MAX_EMAIL = 254
INVALID_CODE = "That code is invalid or has expired."
FAILED = "We couldn't create your account just now. Please try again in a moment."

__all__ = ["SignupError", "UserExistsError", "sign_up"]


class SignupError(Exception):
    """A sign-up that did not happen: `message` is for the person; `wrong_code` says the code was at fault."""

    def __init__(self, message: str, status: int, *, wrong_code: bool = False) -> None:
        """Turn the sign-up away with `message` and `status`."""
        super().__init__(message)
        self.message = message
        self.status = status
        self.wrong_code = wrong_code


def _looks_like_email(email: str) -> bool:
    """One @, text on both sides, a dot inside the domain, no whitespace. Plain string checks, as a regex can backtrack."""
    local, at, domain = email.partition("@")
    return (
        bool(at)
        and bool(local)
        and "@" not in domain
        and not any(c.isspace() for c in email)
        and 0 < domain.find(".") < len(domain) - 1
        and not domain.startswith(".")
        and not domain.endswith(".")
    )


def sign_up(pocket_id: PocketIdClient, code: str, name: str, email: str, return_to: str) -> str:
    """Sign the person up and return the Pocket ID page where they add a passkey, which sends them on to `return_to`.

    An email that already has an account raises UserExistsError, with no token made for it and the code unspent: the
    person logs in as usual. The screen is theirs once they first arrive signed in (pairing.claim_pending).
    """
    if not pairing.code_is_live(code):
        raise SignupError(INVALID_CODE, HTTPStatus.BAD_REQUEST, wrong_code=True)
    if not name or len(name) > MAX_NAME or len(email) > MAX_EMAIL or not _looks_like_email(email):
        msg = "Please enter your name and a valid email."
        raise SignupError(msg, HTTPStatus.BAD_REQUEST)

    try:
        user_id = pocket_id.create_user(email, name)
    except UserExistsError:
        raise
    except PocketIdError as e:
        current_app.logger.error("Sign-up failed: %s", e)  # noqa: TRY400 -- the text is safe; a traceback isn't needed
        raise SignupError(FAILED, HTTPStatus.BAD_GATEWAY) from None
    try:
        token = pocket_id.create_one_time_token(user_id)
    except PocketIdError as e:
        pocket_id.delete_user(user_id)
        current_app.logger.error("Sign-up failed: %s", e)  # noqa: TRY400 -- as above
        raise SignupError(FAILED, HTTPStatus.BAD_GATEWAY) from None
    if pairing.reserve_for_signup(code, user_id) is None:
        # The code was spent between the check and now: take the account back.
        pocket_id.delete_user(user_id)
        raise SignupError(INVALID_CODE, HTTPStatus.BAD_REQUEST, wrong_code=True)
    return pocket_id.setup_url(token, return_to)
