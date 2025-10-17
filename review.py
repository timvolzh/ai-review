#!/usr/bin/env python3
"""
GitLab Merge Request Reviewer powered by Ollama (SSL verification disabled)
"""
from __future__ import annotations

import argparse
import os
import sys
import textwrap
import time
from typing import List, Optional, Tuple

import requests
from dotenv import load_dotenv

from ai_prompt import (
    REVIEW_SYSTEM_PROMPT,
    REVIEW_USER_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)
from llm_client import OllamaError, ollama_generate
from vcs_client import (
    GitHubClient,
    GitHubError,
    GitLabClient,
    GitLabError,
)

# -----------------------------
# Environment & Configuration
# -----------------------------

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

SUPPORTED_VCS = {"gitlab", "github"}
DEFAULT_VCS = (
    os.getenv("REVIEW_VCS")
    or os.getenv("VCS_PROVIDER")
    or "gitlab"
).strip().lower()
if DEFAULT_VCS not in SUPPORTED_VCS:
    DEFAULT_VCS = "gitlab"

GITLAB_URL = os.getenv("GITLAB_URL", "").rstrip("/")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
PROJECT_ID = os.getenv("GITLAB_PROJECT_ID")
VERIFY_GITLAB_SSL = os.getenv("GITLAB_VERIFY_SSL", "false").lower() in {
    "1",
    "true",
    "yes",
}

GITHUB_URL = os.getenv("GITHUB_URL", "https://api.github.com").rstrip("/")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_USER_AGENT = os.getenv("GITHUB_USER_AGENT", "ai-review")
VERIFY_GITHUB_SSL = os.getenv("GITHUB_VERIFY_SSL", "true").lower() in {
    "1",
    "true",
    "yes",
}

_gitlab_client: Optional[GitLabClient] = None
_github_client: Optional[GitHubClient] = None


def _disable_ssl_warnings() -> None:
    requests.packages.urllib3.disable_warnings(  # type: ignore[attr-defined]
        category=requests.packages.urllib3.exceptions.InsecureRequestWarning  # type: ignore[attr-defined]
    )


def get_gitlab_client() -> GitLabClient:
    global _gitlab_client
    if _gitlab_client is None:
        if not GITLAB_URL:
            raise GitLabError("Set GITLAB_URL to interact with GitLab.")
        if not GITLAB_TOKEN:
            raise GitLabError("Set GITLAB_TOKEN to interact with GitLab.")
        client = GitLabClient(
            GITLAB_URL,
            GITLAB_TOKEN,
            verify_ssl=VERIFY_GITLAB_SSL,
        )
        if not VERIFY_GITLAB_SSL:
            _disable_ssl_warnings()
            print(
                "⚠️  SSL verification is DISABLED — use only in trusted environments.",
                file=sys.stderr,
            )
        _gitlab_client = client
    return _gitlab_client


def get_github_client() -> GitHubClient:
    global _github_client
    if _github_client is None:
        if not GITHUB_URL:
            raise GitHubError("Set GITHUB_URL to interact with GitHub.")
        if not GITHUB_TOKEN:
            raise GitHubError("Set GITHUB_TOKEN to interact with GitHub.")
        client = GitHubClient(
            GITHUB_URL,
            GITHUB_TOKEN,
            verify_ssl=VERIFY_GITHUB_SSL,
            user_agent=GITHUB_USER_AGENT,
        )
        if not VERIFY_GITHUB_SSL:
            _disable_ssl_warnings()
            print(
                "⚠️  SSL verification is DISABLED — use only in trusted environments.",
                file=sys.stderr,
            )
        _github_client = client
    return _github_client

# -----------------------------
# Review pipeline
# -----------------------------

MAX_CHARS_PER_CHUNK = 8000
MAX_NOTE_SIZE = 10000

def chunk_diffs(changes: List[dict]) -> List[Tuple[str, List[str]]]:
    chunks, buf, paths, size = [], [], [], 0
    for ch in changes:
        file_header = f"--- a/{ch.get('old_path','')}\n+++ b/{ch.get('new_path','')}\n"
        diff = ch.get("diff", "")
        piece = file_header + diff + "\n\n"
        piece_len = len(piece)
        if size + piece_len > MAX_CHARS_PER_CHUNK and buf:
            chunks.append(("".join(buf), paths.copy()))
            buf.clear(); paths.clear(); size = 0
        buf.append(piece)
        size += piece_len
        paths.append(ch.get("new_path") or ch.get("old_path") or "<unknown>")
    if buf:
        chunks.append(("".join(buf), paths.copy()))
    return chunks

def build_review_for_mr(
    mr: dict,
    changes: List[dict],
    model: Optional[str] = None,
    *,
    item_label: Optional[str] = None,
    item_reference: Optional[str] = None,
) -> str:
    title = mr.get("title", "")
    author_info = mr.get("author") or {}
    author = author_info.get("name") or author_info.get("username")
    if not author:
        user_info = mr.get("user") or {}
        author = (
            user_info.get("name")
            or user_info.get("login")
            or user_info.get("username")
            or "unknown"
        )
    description = (
        mr.get("description")
        or mr.get("body")
        or "(no description)"
    ).strip()
    if not description:
        description = "(no description)"
    if item_reference is None:
        if mr.get("iid") is not None:
            item_reference = f"!{mr['iid']}"
        elif mr.get("number") is not None:
            item_reference = f"#{mr['number']}"
        else:
            item_reference = "<unknown>"
    if item_label is None:
        if item_reference.startswith("!"):
            item_label = "MR"
        elif item_reference.startswith("#"):
            item_label = "PR"
        else:
            item_label = "Change Request"
    chunks = chunk_diffs(changes)
    all_sections = []
    for idx, (chunk_text, paths) in enumerate(chunks, start=1):
        user_prompt = REVIEW_USER_PROMPT_TEMPLATE.format(
            title=title,
            author=author,
            description=description,
            diff_text=chunk_text,
        )
        try:
            section = ollama_generate(
                user_prompt,
                base_url=OLLAMA_URL,
                model=model or OLLAMA_MODEL,
                system=REVIEW_SYSTEM_PROMPT,
            )
        except (OllamaError, requests.RequestException) as e:
            section = f"(Error generating review for chunk {idx}: {e})"
        header = f"## Review chunk {idx}/{len(chunks)} ({', '.join(paths)})"
        all_sections.append(f"{header}\n\n{section}\n")
        time.sleep(0.2)
    summary_prompt = SUMMARY_PROMPT_TEMPLATE.format(
        reviews="\n\n".join(all_sections)[:6000]
    )
    try:
        summary = ollama_generate(
            summary_prompt,
            base_url=OLLAMA_URL,
            model=model or OLLAMA_MODEL,
            system=REVIEW_SYSTEM_PROMPT,
        )
    except (OllamaError, requests.RequestException) as e:
        summary = f"(Error generating summary: {e})"
    header_block = textwrap.dedent(f"""
        # 🤖 AI Code Review (model: {model or OLLAMA_MODEL})
        _This is an automated review. Please verify suggestions before applying._

        **{item_label}:** {item_reference} — **{title}**
        **Author:** {author}

        ---

        ## Overall summary
        {summary}

        ---
        ## Detailed findings
    """).strip()
    return header_block + "\n\n" + "\n\n".join(all_sections)

def split_for_notes(text: str, max_len: int = MAX_NOTE_SIZE) -> List[str]:
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + max_len)
        split_at = text.rfind("\n\n", start, end)
        if split_at == -1 or split_at <= start + 200:
            split_at = end
        parts.append(text[start:split_at].strip())
        start = split_at
    return [p for p in parts if p]

def review_and_comment_gitlab(
    client: GitLabClient,
    project_id: str,
    iid: int,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    mr = client.get_merge_request(project_id, iid)
    changes_doc = client.get_merge_request_changes(project_id, iid)
    changes = changes_doc.get("changes", [])
    if not changes:
        print(f"MR !{iid} has no changes to review.")
        return
    review_md = build_review_for_mr(
        mr,
        changes,
        model=model,
        item_label="MR",
        item_reference=f"!{mr.get('iid', iid)}",
    )
    chunks = split_for_notes(review_md)
    if dry_run:
        print(f"--- DRY RUN: Would post {len(chunks)} note(s) to MR !{iid} ---")
        print(review_md[:2000] + ("..." if len(review_md) > 2000 else ""))
        return
    for i, note in enumerate(chunks, start=1):
        prefix = f"(Part {i}/{len(chunks)})\n\n" if len(chunks) > 1 else ""
        resp = client.post_merge_request_note(project_id, iid, prefix + note)
        url = resp.get("web_url") or resp.get("url") or ""
        print(f"Posted note {i}/{len(chunks)} to MR !{iid}. {url}")


def _split_github_repo(repo: str) -> Tuple[str, str]:
    owner, sep, name = repo.partition("/")
    if not sep or not owner or not name:
        raise GitHubError(
            "Set GITHUB_REPO (owner/name) or pass --github-repo when using GitHub."
        )
    return owner, name


def review_and_comment_github(
    client: GitHubClient,
    owner: str,
    repo: str,
    number: int,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    pr = client.get_pull_request(owner, repo, number)
    files = client.get_pull_request_files(owner, repo, number)
    changes = [
        {
            "old_path": f.get("previous_filename") or f.get("filename"),
            "new_path": f.get("filename"),
            "diff": f.get("patch") or "",
        }
        for f in files
    ]
    if not changes:
        print(f"PR #{number} has no changes to review.")
        return
    review_md = build_review_for_mr(
        pr,
        changes,
        model=model,
        item_label="PR",
        item_reference=f"#{number}",
    )
    chunks = split_for_notes(review_md)
    if dry_run:
        print(f"--- DRY RUN: Would post {len(chunks)} note(s) to PR #{number} ---")
        print(review_md[:2000] + ("..." if len(review_md) > 2000 else ""))
        return
    for i, note in enumerate(chunks, start=1):
        prefix = f"(Part {i}/{len(chunks)})\n\n" if len(chunks) > 1 else ""
        resp = client.post_pull_request_comment(owner, repo, number, prefix + note)
        url = resp.get("html_url") or resp.get("url") or ""
        print(f"Posted note {i}/{len(chunks)} to PR #{number}. {url}")

# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AI MR/PR Reviewer using Ollama (GitLab SSL disabled by default)"
    )
    g_target = p.add_mutually_exclusive_group(required=True)
    g_target.add_argument("--iid", type=int, help="Review a specific MR/PR by numeric ID")
    g_target.add_argument("--list", action="store_true", help="List open change requests and exit")
    g_target.add_argument("--all", action="store_true", help="Review all open change requests")
    p.add_argument("--model", default=OLLAMA_MODEL, help=f"Ollama model to use (default: {OLLAMA_MODEL})")
    p.add_argument("--dry-run", action="store_true", help="Don't post notes; print a preview")
    p.add_argument(
        "--vcs",
        choices=sorted(SUPPORTED_VCS),
        default=DEFAULT_VCS,
        help="Version control provider to target (default from REVIEW_VCS/VCS_PROVIDER or gitlab)",
    )
    p.add_argument(
        "--github-repo",
        help="owner/name for the GitHub repository when using --vcs github",
    )
    return p.parse_args()

def main() -> None:
    args = parse_args()
    provider = args.vcs
    if provider == "gitlab":
        if args.list:
            if not PROJECT_ID:
                print("Set GITLAB_PROJECT_ID to list MRs.")
                sys.exit(2)
            try:
                client = get_gitlab_client()
            except GitLabError as exc:
                print(str(exc))
                sys.exit(2)
            mrs = client.list_open_merge_requests(PROJECT_ID)
            if not mrs:
                print("No open merge requests.")
                return
            for mr in mrs:
                print(
                    f"!{mr['iid']}: {mr['title']} (by {mr['author']['name']}) — {mr['web_url']}"
                )
            return
        if args.iid:
            if not (PROJECT_ID and GITLAB_URL and GITLAB_TOKEN):
                print("Set GITLAB_URL, GITLAB_TOKEN, and GITLAB_PROJECT_ID to review an MR.")
                sys.exit(2)
            try:
                client = get_gitlab_client()
            except GitLabError as exc:
                print(str(exc))
                sys.exit(2)
            review_and_comment_gitlab(
                client,
                PROJECT_ID,
                args.iid,
                model=args.model,
                dry_run=args.dry_run,
            )
            return
        if args.all:
            if not (PROJECT_ID and GITLAB_URL and GITLAB_TOKEN):
                print("Set GITLAB_URL, GITLAB_TOKEN, and GITLAB_PROJECT_ID to review MRs.")
                sys.exit(2)
            try:
                client = get_gitlab_client()
            except GitLabError as exc:
                print(str(exc))
                sys.exit(2)
            mrs = client.list_open_merge_requests(PROJECT_ID)
            for mr in mrs:
                print(f"\n=== Reviewing MR !{mr['iid']}: {mr['title']} ===")
                review_and_comment_gitlab(
                    client,
                    PROJECT_ID,
                    mr["iid"],
                    model=args.model,
                    dry_run=args.dry_run,
                )
            return
    elif provider == "github":
        repo_value = args.github_repo or GITHUB_REPO
        if not repo_value:
            print(
                "Set GITHUB_REPO (owner/name) or pass --github-repo to target a repository."
            )
            sys.exit(2)
        try:
            owner, repo = _split_github_repo(repo_value)
            client = get_github_client()
        except GitHubError as exc:
            print(str(exc))
            sys.exit(2)
        if args.list:
            pulls = client.list_open_pull_requests(owner, repo)
            if not pulls:
                print("No open pull requests.")
                return
            for pr in pulls:
                user = pr.get("user") or {}
                print(
                    f"#{pr['number']}: {pr['title']} (by {user.get('login', 'unknown')}) — {pr.get('html_url', pr.get('url', ''))}"
                )
            return
        if args.iid:
            review_and_comment_github(
                client,
                owner,
                repo,
                args.iid,
                model=args.model,
                dry_run=args.dry_run,
            )
            return
        if args.all:
            pulls = client.list_open_pull_requests(owner, repo)
            for pr in pulls:
                print(f"\n=== Reviewing PR #{pr['number']}: {pr['title']} ===")
                review_and_comment_github(
                    client,
                    owner,
                    repo,
                    pr["number"],
                    model=args.model,
                    dry_run=args.dry_run,
                )
            return
    else:
        print(f"Unsupported VCS provider: {provider}")
        sys.exit(2)

if __name__ == "__main__":
    main()
