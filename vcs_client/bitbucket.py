"""Bitbucket API client utilities."""
from __future__ import annotations

from typing import Iterable, List, Optional

import requests


class BitbucketError(RuntimeError):
    """Raised when Bitbucket API interaction fails."""


class BitbucketClient:
    """Minimal Bitbucket API client for pull request operations."""

    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        *,
        timeout: int = 30,
        verify_ssl: bool = True,
    ) -> None:
        if not base_url:
            raise BitbucketError("Bitbucket base URL is required")
        if not username:
            raise BitbucketError("Bitbucket username is required")
        if not app_password:
            raise BitbucketError("Bitbucket app password is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.auth = (username, app_password)
        self._session.verify = verify_ssl

    # -----------------------------
    # Low-level helpers
    # -----------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
    ) -> requests.Response:
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        resp = self._session.request(
            method,
            url,
            params=params,
            json=json,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            snippet = resp.text[:200]
            raise BitbucketError(
                f"{method.upper()} {url} failed: {resp.status_code} {snippet}"
            )
        return resp

    def _get(self, path: str, *, params: Optional[dict] = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def _post(self, path: str, *, json: dict) -> requests.Response:
        return self._request("POST", path, json=json)

    # -----------------------------
    # High-level helpers
    # -----------------------------

    @staticmethod
    def _split_repo_identifier(repo_identifier: str) -> tuple[str, str]:
        try:
            workspace, repo_slug = repo_identifier.split("/", 1)
        except ValueError as exc:  # pragma: no cover - defensive branch
            raise BitbucketError(
                "Bitbucket repository must be provided as 'workspace/repo_slug'"
            ) from exc
        return workspace, repo_slug

    @staticmethod
    def _normalize_pr(pr: dict) -> dict:
        normalized = pr.copy()
        normalized["iid"] = pr.get("id")
        author = pr.get("author") or {}
        normalized["author"] = {
            "name": author.get("display_name")
            or author.get("nickname")
            or "unknown",
        }
        links = pr.get("links") or {}
        html_link = links.get("html") or {}
        normalized["web_url"] = html_link.get("href", "")
        return normalized

    @staticmethod
    def _parse_diff(diff_text: str) -> List[dict]:
        changes: List[dict] = []
        current: Optional[dict] = None
        buf: List[str] = []
        for line in diff_text.splitlines(keepends=True):
            if line.startswith("diff --git"):
                if current is not None:
                    current["diff"] = "".join(buf).strip("\n")
                    changes.append(current)
                parts = line.split()
                old_path = parts[2][2:] if len(parts) >= 3 else ""
                new_path = parts[3][2:] if len(parts) >= 4 else ""
                current = {
                    "old_path": old_path,
                    "new_path": new_path,
                }
                buf = []
            elif current is not None:
                buf.append(line)
        if current is not None:
            current["diff"] = "".join(buf).strip("\n")
            changes.append(current)
        if not changes and diff_text:
            changes.append({"old_path": "", "new_path": "", "diff": diff_text})
        return changes

    # -----------------------------
    # Public API
    # -----------------------------

    def list_open_merge_requests(
        self, repo_identifier: str, *, pagelen: int = 50
    ) -> List[dict]:
        workspace, repo_slug = self._split_repo_identifier(repo_identifier)
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests"
        params = {"state": "OPEN", "pagelen": pagelen}
        results: List[dict] = []
        while True:
            resp = self._get(path, params=params)
            data = resp.json()
            values: Iterable[dict] = data.get("values", [])
            results.extend(self._normalize_pr(pr) for pr in values)
            next_link = data.get("next")
            if not next_link:
                break
            path = next_link
            params = None
        return results

    def get_merge_request(self, repo_identifier: str, iid: int) -> dict:
        workspace, repo_slug = self._split_repo_identifier(repo_identifier)
        path = f"/rest/api/1.0/projects/{workspace}/repos/{repo_slug}/pull-requests/{iid}"
        pr = self._get(path).json()
        return self._normalize_pr(pr)

    def get_merge_request_changes(self, repo_identifier: str, iid: int) -> dict:
        workspace, repo_slug = self._split_repo_identifier(repo_identifier)
        path = (
            f"/rest/api/1.0/projects/{workspace}/repos/{repo_slug}/pull-requests/{iid}/diff"
        )
        diff_text = self._get(path).text
        return {"changes": self._parse_diff(diff_text)}

    def post_merge_request_note(
        self, repo_identifier: str, iid: int, body_markdown: str
    ) -> dict:
        workspace, repo_slug = self._split_repo_identifier(repo_identifier)
        path = (
            f"/rest/api/1.0/projects/{workspace}/repos/{repo_slug}/pull-requests/{iid}/comments"
        )

        #payload = {"content": {"raw": body_markdown}}
        payload = {"text": body_markdown}
        resp = self._post(path, json=payload).json()
        links = resp.get("links") or {}
        html_link = links.get("html") or {}
        return {"web_url": html_link.get("href", ""), "id": resp.get("id")}

