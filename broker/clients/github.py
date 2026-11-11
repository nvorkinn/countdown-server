"""Talks to GitHub for the developer panel, with a device owner's token.

The panel shows how many repos the owner has, their open pull requests, the ones waiting for their review, which repos
are failing on their default branch, any release running (or just finished) with its jobs as GitHub's graph, and their
Copilot premium requests this month.
"""

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import requests
from redis import RedisError

from broker.clients import workflow_graph

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"
TIMEOUT = 10
# Only repos pushed to this recently are checked for failing builds: older ones are settled, and each costs a call.
ACTIVE = timedelta(days=30)
MOST_CHECKED = 20
PAGE_SIZE = 100
# What a finished run ends in when it didn't pass.
FAILED = {"failure", "timed_out", "startup_failure"}
# A workflow is a release if its name says so.
RELEASE = re.compile(r"release|deploy", re.IGNORECASE)
# Runs started this recently are looked through for a release: one takes minutes, so this is plenty.
RECENT_RUNS = timedelta(hours=6)
# A finished release stays on the screen this long, so its result is seen. The developer provider keeps the fast pace
# while it shows, so it goes within about its BURST_INTERVAL_S of this.
SHOWN_AFTER = timedelta(minutes=1)
# How long to leave GitHub alone after a rate limit that doesn't say how long: GitHub asks for at least a minute.
DEFAULT_BACKOFF_S = 60


def summary(token: str, cache: Redis | None = None) -> dict:
    """Return the owner's GitHub at a glance, the developer panel's `github`.

    Read through the ETag `cache` when given, so the calls whose answer hasn't changed since the last poll
    cost none of the token's rate limit. Raises requests.HTTPError if GitHub refuses the token or fails.
    """
    api = Api(token, cache)
    try:
        login = api.get("/user")["login"]
        repos = [
            repo for repo in api.pages("/user/repos", affiliation="owner", sort="pushed") if not repo.get("archived")
        ]
        recent = _recently_pushed(repos)
        return {
            "login": login,
            "repos": len(repos),
            "open_prs": _count(api, f"is:pr is:open author:{login}"),
            "review_requests": _count(api, f"is:pr is:open review-requested:{login}"),
            "failing": [repo["name"] for repo in recent if _failing(api, repo)],
            "releases": _releases(api, recent),
        }
    finally:
        api.close()


def copilot_premium_requests(token: str, login: str) -> int | None:
    """Return the Copilot premium requests used this month, or None if GitHub won't say.

    It only says for a plan the owner pays for themselves, and only to a token with the Plan permission. Raises
    requests.HTTPError if GitHub is rate-limiting the token (which isn't GitHub not saying).
    """
    response = _session(token).get(
        f"{API_URL}/users/{login}/settings/billing/premium_request/usage",
        params={"product": "Copilot"},
        timeout=TIMEOUT,
    )
    if not response.ok:
        _raise_if_rate_limited(response)
        return None
    return round(sum(item.get("grossQuantity", 0) for item in response.json().get("usageItems", [])))


def rate_limit_wait(error: Exception, now: float | None = None) -> float | None:
    """Return how many seconds GitHub asks to be left alone if `error` is its rate limit; None if it's anything else.

    GitHub answers a rate-limited call with 429 or 403 (a 403 is also a token without the permission, which isn't
    one): with Retry-After (seconds) for a secondary limit, or X-RateLimit-Remaining 0 and X-RateLimit-Reset (epoch
    seconds) for the 5,000 an hour. Without either, at least a minute. `now` is the epoch time (for the tests).
    """
    response = error.response if isinstance(error, requests.HTTPError) else None
    if response is None or response.status_code not in {HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS}:
        return None
    headers = response.headers or {}
    if (retry := str(headers.get("Retry-After", ""))).isdigit():
        return max(int(retry), 1)
    reset = str(headers.get("X-RateLimit-Reset", ""))
    if headers.get("X-RateLimit-Remaining") == "0" and reset.isdigit():
        return max(int(reset) - (time.time() if now is None else now), 1)
    if response.status_code == HTTPStatus.TOO_MANY_REQUESTS or "rate limit" in (response.text or "").lower():
        return DEFAULT_BACKOFF_S
    return None


def _raise_if_rate_limited(response: requests.Response) -> None:
    """Raise requests.HTTPError if `response` is GitHub's rate limit, which the callers that skip a failure mustn't."""
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        if rate_limit_wait(e) is not None:
            raise


def _session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    return session


def _count(api: Api, query: str) -> int:
    return api.get("/search/issues", q=query, per_page=1)["total_count"]


def _recently_pushed(repos: list[dict]) -> list[dict]:
    since = datetime.now(UTC) - ACTIVE
    recent = [repo for repo in repos if repo.get("pushed_at") and datetime.fromisoformat(repo["pushed_at"]) >= since]
    return recent[:MOST_CHECKED]


def _failing(api: Api, repo: dict) -> bool:
    """Whether any workflow's latest finished run on the repo's default branch failed."""
    runs = api.get(
        f"/repos/{repo['full_name']}/actions/runs",
        branch=repo["default_branch"],
        status="completed",
        exclude_pull_requests="true",
        per_page=20,
    )["workflow_runs"]
    latest: dict[int, str] = {}
    for run in runs:  # newest first
        latest.setdefault(run["workflow_id"], run["conclusion"])
    return any(conclusion in FAILED for conclusion in latest.values())


def _releases(api: Api, repos: list[dict]) -> list[dict]:
    """Return each repo's release that's running or finished in the last SHOWN_AFTER, the latest started first.

    A repo whose release can't be read is left out, so the rest of the panel still shows; but a rate limit is raised
    (requests.HTTPError), as every call after it would fail too and the panel would lose its releases for nothing.
    """
    now = datetime.now(UTC)
    found = []
    for repo in repos:
        try:
            release = _release(api, repo, now)
        except requests.RequestException as e:
            if rate_limit_wait(e) is not None:
                raise
            logger.warning("Couldn't read %s's release: %s", repo["full_name"], e)
            continue
        if release is not None:
            found.append(release)
    return sorted(found, key=lambda release: release["started"], reverse=True)


def _release(api: Api, repo: dict, now: datetime) -> dict | None:
    """Return the repo's latest release run with its jobs as graph nodes; None if none, or it finished a while ago."""
    runs = api.get(
        f"/repos/{repo['full_name']}/actions/runs",
        created=f">={now - RECENT_RUNS:%Y-%m-%dT%H:%M:%SZ}",
        per_page=30,
    )["workflow_runs"]
    run = next((run for run in runs if RELEASE.search(run["name"])), None)  # newest first
    if run is None:
        return None
    finished = run["status"] == "completed"
    if finished and datetime.fromisoformat(run["updated_at"]) < now - SHOWN_AFTER:
        return None
    jobs = api.get(f"/repos/{repo['full_name']}/actions/runs/{run['id']}/jobs", per_page=100)["jobs"]
    return {
        "repo": repo["name"],
        "title": _title(run, repo),
        "state": workflow_graph.run_state(run),
        "started": run["run_started_at"],
        "finished": run["updated_at"] if finished else None,
        "jobs": workflow_graph.nodes(_workflow_jobs(api, repo, run), jobs),
    }


def _title(run: dict, repo: dict) -> str:
    """Return the tag a release was pushed for, else its run number (one started by hand runs on the default branch)."""
    if run["event"] == "push" and run["head_branch"] != repo["default_branch"]:
        return run["head_branch"]
    return f"#{run['run_number']}"


def _workflow_jobs(api: Api, repo: dict, run: dict) -> list[workflow_graph.WorkflowJob]:
    """Return the jobs of the run's workflow file, as at the run's commit; none if it can't be read.

    Without them the graph still shows the run's jobs, but not the lines between them. A rate limit is raised
    (requests.HTTPError), as in _releases.
    """
    path = run["path"].split("@")[0]
    try:
        return workflow_graph.workflow_jobs(
            api.read(f"/repos/{repo['full_name']}/contents/{path}", raw=True, ref=run["head_sha"])
        )
    except (requests.RequestException, ValueError) as e:
        if rate_limit_wait(e) is not None:
            raise
        logger.warning("Couldn't read %s's %s: %s", repo["full_name"], path, e)
        return []


class UnexpectedResponseError(ValueError):
    """GitHub answered with JSON of a shape the panel doesn't read."""


class Api:
    """GitHub's REST API as one token's user, for one poll: JSON reads through an ETag cache."""

    def __init__(self, token: str, cache: Redis | None = None) -> None:
        """Open a session for one poll."""
        self.session = _session(token)
        self.cache = cache
        self.cache_scope = hashlib.sha256(token.encode()).hexdigest()

    def close(self) -> None:
        """Release the poll's HTTP connections."""
        self.session.close()

    def read(self, path: str, *, raw: bool = False, **params: str | int) -> str:
        """Return the text at `path`, revalidating a cached ETag: GitHub doesn't count authenticated 304s."""
        identity = json.dumps([self.cache_scope, path, params, raw], sort_keys=True)
        key = f"github-etag:{hashlib.sha256(identity.encode()).hexdigest()}"
        cached = None
        if self.cache is not None:
            try:
                if value := self.cache.get(key):
                    cached = json.loads(value)
            except RedisError:
                logger.warning("GitHub ETag cache read failed", exc_info=True)
        headers = {"Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"}
        if cached:
            headers["If-None-Match"] = cached["etag"]
        response = self.session.get(f"{API_URL}{path}", params=params, headers=headers, timeout=TIMEOUT)
        if response.status_code == HTTPStatus.NOT_MODIFIED and cached:
            text = cached["text"]
        else:
            response.raise_for_status()
            text = response.text
            if self.cache is not None and (etag := response.headers.get("ETag")):
                try:
                    self.cache.set(key, json.dumps({"etag": etag, "text": text}), ex=3600)
                except RedisError:
                    logger.warning("GitHub ETag cache write failed", exc_info=True)
        return text

    def get(self, path: str, **params: str | int) -> dict:
        """Read a JSON object, refusing an unexpected upstream response shape."""
        data = json.loads(self.read(path, raw=False, **params))
        if not isinstance(data, dict):
            msg = "GitHub returned an unexpected response shape."
            raise UnexpectedResponseError(msg)
        return data

    def pages(self, path: str, key: str | None = None, **params: str | int) -> list[dict]:
        """Read all pages of a list endpoint, including nested lists such as check_runs."""
        found = []
        page = 1
        while True:
            data = json.loads(self.read(path, raw=False, **params, per_page=PAGE_SIZE, page=page))
            batch = data[key] if key else data
            if not isinstance(batch, list):
                msg = "GitHub returned an unexpected list response."
                raise UnexpectedResponseError(msg)
            found.extend(batch)
            if len(batch) < PAGE_SIZE:
                return found
            page += 1
