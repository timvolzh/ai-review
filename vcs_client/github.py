"""GitHub API client utilities."""
from __future__ import annotations

from typing import List, Optional

import requests


class GitHubError(RuntimeError):
    """Raised when GitHub API interaction fails."""


class GitHubClient:
    """Minimal GitHub API client for pull request operations."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: int = 30,
        verify_ssl: bool = True,
        user_agent: str = "ai-review",
    ) -> None:
        if not base_url:
            raise GitHubError("GitHub base URL is required")
        if not token:
            raise GitHubError("GitHub access token is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": user_agent,
            }
        )

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._base_url}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
    ) -> requests.Response:
        resp = self._session.request(
            method,
            self._url(path),
            params=params,
            json=json,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            raise GitHubError(
                f"{method} {path} failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp

    def list_open_pull_requests(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int = 50,
    ) -> List[dict]:
        """Return all open pull requests for the repository sorted by last update."""

        pulls: List[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls",
                params={
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": per_page,
                    "page": page,
                },
            )
            batch = resp.json()
            if not batch:
                break
            pulls.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return pulls

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict:
        """Fetch metadata for a pull request."""

        return self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}"
        ).json()

    def get_pull_request_files(
        self, owner: str, repo: str, number: int, *, per_page: int = 100
    ) -> List[dict]:
        """Return the list of changed files for a pull request."""

        files: List[dict] = []
        page = 1
        while True:
            resp = self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls/{number}/files",
                params={"per_page": per_page, "page": page},
            )
            batch = resp.json()
            if not batch:
                break
            files.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return files

    def post_pull_request_comment(
        self, owner: str, repo: str, number: int, body_markdown: str
    ) -> dict:
        """Post a comment to the pull request conversation."""

        if not body_markdown:
            raise GitHubError("Comment body must not be empty")
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": body_markdown},
        ).json()

