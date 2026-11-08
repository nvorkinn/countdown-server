"""Talks to Pocket ID's admin API, to sign a new owner up: create their account, then a one-time token to set a passkey.

Endpoints, checked against Pocket ID's source (v2.x, backend/internal):
- POST /api/users            {username, email, firstName, lastName, displayName, userGroupIds}  -> 201 {id, ...};
  409 (code `already_in_use`, detail property email/username) when the email or username is taken.
- POST /api/users/:id/one-time-access-token  {ttl: "10m"}  -> 201 {token}.
- DELETE /api/users/:id, to take back an account whose sign-up didn't finish.
The API key goes in the `X-API-Key` header. The account setup page is `<POCKET_ID_URL>/lc/<token>?redirect=<url>`, which
signs the person in with the token, asks them to add a passkey, and then sends them on to `redirect`.

The group is given at creation (`userGroupIds`) rather than with PUT /api/user-groups/:id/users, because that call
*replaces* the group's members with the list sent, so adding one user would need a read-modify-write that can drop
people added in between. A new user has no groups, so setting them at creation can't remove anyone.

The API key is a server secret: it is only ever sent to Pocket ID, and never logged or put in an error message.
"""

import hashlib
import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import requests

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

TIMEOUT = (3.05, 10)  # connect, read: a sign-up page mustn't hang on Pocket ID
# How long the one-time token works. It is used at once, by the redirect that follows, so this is short.
TOKEN_TTL = "10m"  # noqa: S105 -- a duration, not a password
# Pocket ID's own limit on a username.
MAX_USERNAME = 50


class PocketIdError(Exception):
    """Pocket ID couldn't be reached, or answered something unexpected. The text is safe to log."""


class PocketIdNotConfiguredError(PocketIdError):
    """POCKET_ID_URL, POCKET_ID_API_KEY or POCKET_ID_COUNTDOWN_GROUP_ID isn't set."""


class UserExistsError(PocketIdError):
    """A user with this email (or username) already exists."""


def username_for(email: str) -> str:
    """Return the username for an email: the email itself, which Pocket ID allows, or a shortened one with a hash."""
    if len(email) <= MAX_USERNAME:
        return email
    return f"{email[:40]}-{hashlib.sha256(email.encode()).hexdigest()[:8]}"


class PocketIdClient:
    """The few admin calls a sign-up needs."""

    def __init__(self, base_url: str, api_key: str, group_id: str, session: requests.Session | None = None) -> None:
        """Talk to the Pocket ID at `base_url` with an admin API key, adding new users to the group `group_id`."""
        if not (base_url and api_key and group_id):
            msg = "Pocket ID isn't configured"
            raise PocketIdNotConfiguredError(msg)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._group_id = group_id
        self._session = session or requests.Session()

    def create_user(self, email: str, name: str) -> str:
        """Create a user in the countdown group and return their id.

        Raises UserExistsError if the email is already registered: the caller must then send the person to log in, and
        never issue a token for the existing account.
        """
        first, _, last = name.partition(" ")
        body = {
            "username": username_for(email),
            "email": email,
            "emailVerified": False,
            "firstName": first[:50],
            "lastName": last.strip()[:50],
            "displayName": name[:100],
            "isAdmin": False,
            "userGroupIds": [self._group_id],
        }
        response = self._request("POST", "/api/users", body)
        if response.status_code == HTTPStatus.CONFLICT:
            raise UserExistsError
        if response.status_code != HTTPStatus.CREATED:
            msg = "create user"
            raise self._unexpected(msg, response)
        user_id = self._json(response).get("id")
        if not isinstance(user_id, str) or not user_id:
            msg = "Pocket ID create user: no user id in the answer"
            raise PocketIdError(msg)
        return user_id

    def create_one_time_token(self, user_id: str) -> str:
        """Create a one-time access token for the user, which lasts TOKEN_TTL."""
        path = f"/api/users/{quote(user_id, safe='')}/one-time-access-token"
        response = self._request("POST", path, {"ttl": TOKEN_TTL})
        if response.status_code != HTTPStatus.CREATED:
            msg = "create token"
            raise self._unexpected(msg, response)
        token = self._json(response).get("token")
        if not isinstance(token, str) or not token:
            msg = "Pocket ID create token: no token in the answer"
            raise PocketIdError(msg)
        return token

    def delete_user(self, user_id: str) -> None:
        """Delete a user whose sign-up didn't finish, best effort: a failure is logged, not raised."""
        try:
            response = self._request("DELETE", f"/api/users/{quote(user_id, safe='')}")
        except PocketIdError as error:
            logger.warning("Couldn't clean up a Pocket ID user: %s", error)
            return
        if response.status_code not in (HTTPStatus.NO_CONTENT, HTTPStatus.OK):
            logger.warning("Couldn't clean up a Pocket ID user: HTTP %s", response.status_code)

    def setup_url(self, token: str, return_to: str) -> str:
        """Return the page where the person adds a passkey with `token`, and is then sent on to `return_to`."""
        return f"{self._base_url}/lc/{quote(token, safe='')}?redirect={quote(return_to, safe='')}"

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> requests.Response:
        try:
            return self._session.request(
                method,
                self._base_url + path,
                json=body,
                headers={"X-API-Key": self._api_key, "Accept": "application/json"},
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            # The type only: the text of a request error can quote the URL, and nothing here needs more.
            msg = f"Pocket ID {method} {path}: {type(error).__name__}"
            raise PocketIdError(msg) from None

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            msg = "Pocket ID answered something that isn't JSON"
            raise PocketIdError(msg) from None
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _unexpected(action: str, response: requests.Response) -> PocketIdError:
        return PocketIdError(f"Pocket ID {action}: HTTP {response.status_code}")
