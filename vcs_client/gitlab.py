"""GitLab API client utilities."""
from __future__ import annotations

from typing import List, Optional

import requests


class GitLabError(RuntimeError):
    """Raised when GitLab API interaction fails."""


class GitLabClient:
    """Minimal GitLab API client for merge request operations."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: int = 30,
        verify_ssl: bool = True,
    ) -> None:
        if not base_url:
            raise GitLabError("GitLab base URL is required")
        if not token:
            raise GitLabError("GitLab access token is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._session.headers.update(
            {
                "PRIVATE-TOKEN": token,
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self._base_url}/api/v4{path}"

    def _get(self, path: str, *, params: Optional[dict] = None) -> requests.Response:
        resp = self._session.get(self._url(path), params=params, timeout=self._timeout)
        if resp.status_code >= 400:
            raise GitLabError(
                f"GET {path} failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp

    def _post(self, path: str, *, json: dict) -> requests.Response:
        resp = self._session.post(self._url(path), json=json, timeout=self._timeout)
        if resp.status_code >= 400:
            raise GitLabError(
                f"POST {path} failed: {resp.status_code} {resp.text[:200]}"
            )
        return resp

    def list_open_merge_requests(self, project_id: str, *, per_page: int = 50) -> List[dict]:
        """Return all open merge requests for the project sorted by last update."""

        mrs: List[dict] = []
        page = 1
        while True:
            resp = self._get(
                f"/projects/{project_id}/merge_requests",
                params={
                    "state": "opened",
                    "per_page": per_page,
                    "page": page,
                    "order_by": "updated_at",
                    "sort": "desc",
                },
            )
            batch = resp.json()
            if not batch:
                break
            mrs.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return mrs

    def get_merge_request(self, project_id: str, iid: int) -> dict:
        """Fetch metadata for a merge request."""

        return self._get(f"/projects/{project_id}/merge_requests/{iid}").json()

    def get_merge_request_changes(self, project_id: str, iid: int) -> dict:
        """Fetch the change set for a merge request."""

        return self._get(f"/projects/{project_id}/merge_requests/{iid}/changes").json()

    def post_merge_request_note(self, project_id: str, iid: int, body_markdown: str) -> dict:
        """Post a review note to the merge request."""

        return self._post(
            f"/projects/{project_id}/merge_requests/{iid}/notes",
            json={"body": body_markdown},
        ).json()

