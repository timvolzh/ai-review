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
from typing import Any, List, Optional, Tuple

import requests
from dotenv import load_dotenv

from ai_prompt import (
    REVIEW_SYSTEM_PROMPT,
    REVIEW_USER_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)
from llm_client import OllamaError, ollama_generate
from vcs_client import (
    BitbucketClient,
    BitbucketError,
    GitLabClient,
    GitLabError,
)

# -----------------------------
# Environment & Configuration
# -----------------------------

load_dotenv()

VCS_PROVIDER = os.getenv("VCS_PROVIDER", "gitlab").strip().lower()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

GITLAB_URL = os.getenv("GITLAB_URL", "").rstrip("/")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
GITLAB_PROJECT_ID = os.getenv("GITLAB_PROJECT_ID")
VERIFY_GITLAB_SSL = os.getenv("GITLAB_VERIFY_SSL", "false").lower() in {
    "1",
    "true",
    "yes",
}

BITBUCKET_URL = os.getenv("BITBUCKET_URL", "https://api.bitbucket.org").rstrip("/")
BITBUCKET_USERNAME = os.getenv("BITBUCKET_USERNAME")
BITBUCKET_APP_PASSWORD = os.getenv("BITBUCKET_APP_PASSWORD")
BITBUCKET_WORKSPACE = os.getenv("BITBUCKET_WORKSPACE")
BITBUCKET_REPO_SLUG = os.getenv("BITBUCKET_REPO_SLUG")
VERIFY_BITBUCKET_SSL = os.getenv("BITBUCKET_VERIFY_SSL", "true").lower() in {
    "1",
    "true",
    "yes",
}

_gitlab_client: Optional[GitLabClient] = None
_bitbucket_client: Optional[BitbucketClient] = None


def _warn_ssl_disabled(provider_name: str) -> None:
    requests.packages.urllib3.disable_warnings(  # type: ignore[attr-defined]
        category=requests.packages.urllib3.exceptions.InsecureRequestWarning  # type: ignore[attr-defined]
    )
    print(
        f"⚠️  SSL verification is DISABLED for {provider_name} — use only in trusted environments.",
        file=sys.stderr,
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
            _warn_ssl_disabled("GitLab")
        _gitlab_client = client
    return _gitlab_client


def get_bitbucket_client() -> BitbucketClient:
    global _bitbucket_client
    if _bitbucket_client is None:
        if not BITBUCKET_USERNAME:
            raise BitbucketError("Set BITBUCKET_USERNAME to interact with Bitbucket.")
        if not BITBUCKET_APP_PASSWORD:
            raise BitbucketError(
                "Set BITBUCKET_APP_PASSWORD to interact with Bitbucket."
            )
        client = BitbucketClient(
            BITBUCKET_URL,
            BITBUCKET_USERNAME,
            BITBUCKET_APP_PASSWORD,
            verify_ssl=VERIFY_BITBUCKET_SSL,
        )
        if not VERIFY_BITBUCKET_SSL:
            _warn_ssl_disabled("Bitbucket")
        _bitbucket_client = client
    return _bitbucket_client


def get_vcs_client(provider: str):
    if provider == "gitlab":
        return get_gitlab_client()
    if provider == "bitbucket":
        return get_bitbucket_client()
    raise RuntimeError(f"Unsupported VCS provider: {provider}")


def resolve_project_identifier(provider: str) -> str:
    if provider == "gitlab":
        return GITLAB_PROJECT_ID or ""
    if provider == "bitbucket":
        if BITBUCKET_WORKSPACE and BITBUCKET_REPO_SLUG:
            return f"{BITBUCKET_WORKSPACE}/{BITBUCKET_REPO_SLUG}"
        return ""
    return ""


def missing_target_message(provider: str) -> str:
    if provider == "gitlab":
        return "Set GITLAB_PROJECT_ID to select a project."
    if provider == "bitbucket":
        return "Set BITBUCKET_WORKSPACE and BITBUCKET_REPO_SLUG to select a repository."
    return "Unknown VCS provider; set VCS_PROVIDER or --vcs."


def format_request_id(provider: str, iid: Any) -> str:
    prefix = "!" if provider == "gitlab" else "#"
    if iid is None:
        return f"{prefix}?"
    return f"{prefix}{iid}"

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
    *,
    model: Optional[str] = None,
    provider: str = "gitlab",
) -> str:
    title = mr.get("title", "")
    author = (mr.get("author") or {}).get("name", "unknown")
    description = (mr.get("description") or "(no description)").strip()
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
    request_label = format_request_id(provider, mr.get("iid"))
    header_block = textwrap.dedent(f"""
        # 🤖 AI Code Review (model: {model or OLLAMA_MODEL})
        _This is an automated review. Please verify suggestions before applying._

        **Request:** {request_label} — **{title}**
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

def review_and_comment(
    client: Any,
    target_identifier: str,
    iid: int,
    model: Optional[str] = None,
    dry_run: bool = False,
    provider: str = "gitlab",
) -> None:
    mr = client.get_merge_request(target_identifier, iid)
    changes_doc = client.get_merge_request_changes(target_identifier, iid)
    changes = changes_doc.get("changes", [])
    if not changes:
        print(f"Request {format_request_id(provider, iid)} has no changes to review.")
        return
    review_md = build_review_for_mr(
        mr,
        changes,
        model=model,
        provider=provider,
    )
    chunks = split_for_notes(review_md)
    if dry_run:
        request_label = format_request_id(provider, iid)
        print(
            f"--- DRY RUN: Would post {len(chunks)} note(s) to request {request_label} ---"
        )
        print(review_md[:2000] + ("..." if len(review_md) > 2000 else ""))
        return
    for i, note in enumerate(chunks, start=1):
        prefix = f"(Part {i}/{len(chunks)})\n\n" if len(chunks) > 1 else ""
        resp = client.post_merge_request_note(target_identifier, iid, prefix + note)
        url = resp.get("web_url") or resp.get("url") or ""
        request_label = format_request_id(provider, iid)
        print(f"Posted note {i}/{len(chunks)} to request {request_label}. {url}")

# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AI reviewer for GitLab or Bitbucket powered by Ollama",
    )
    g_target = p.add_mutually_exclusive_group(required=True)
    g_target.add_argument("--iid", type=int, help="Review a specific request by IID")
    g_target.add_argument("--list", action="store_true", help="List open requests and exit")
    g_target.add_argument("--all", action="store_true", help="Review all open requests")
    p.add_argument(
        "--vcs",
        choices=["gitlab", "bitbucket"],
        default=VCS_PROVIDER,
        help="VCS provider to use (default: %(default)s or VCS_PROVIDER env)",
    )
    p.add_argument("--model", default=OLLAMA_MODEL, help=f"Ollama model to use (default: {OLLAMA_MODEL})")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't post to the VCS; print a preview",
    )
    return p.parse_args()

def main() -> None:
    args = parse_args()
    provider = args.vcs.lower()
    target_identifier = resolve_project_identifier(provider)
    error_types = (GitLabError, BitbucketError, RuntimeError)

    def get_client_or_exit() -> Any:
        try:
            return get_vcs_client(provider)
        except error_types as exc:
            print(str(exc))
            sys.exit(2)

    if args.list:
        if not target_identifier:
            print(missing_target_message(provider))
            sys.exit(2)
        client = get_client_or_exit()
        mrs = client.list_open_merge_requests(target_identifier)
        if not mrs:
            print("No open merge or pull requests.")
            return
        for mr in mrs:
            label = format_request_id(provider, mr.get("iid"))
            author = (mr.get("author") or {}).get("name", "unknown")
            url = mr.get("web_url", "")
            print(f"{label}: {mr['title']} (by {author}) — {url}")
        return
    if args.iid:
        if not target_identifier:
            print(missing_target_message(provider))
            sys.exit(2)
        client = get_client_or_exit()
        review_and_comment(
            client,
            target_identifier,
            args.iid,
            model=args.model,
            dry_run=args.dry_run,
            provider=provider,
        )
        return
    if args.all:
        if not target_identifier:
            print(missing_target_message(provider))
            sys.exit(2)
        client = get_client_or_exit()
        mrs = client.list_open_merge_requests(target_identifier)
        for mr in mrs:
            label = format_request_id(provider, mr.get("iid"))
            print(f"\n=== Reviewing request {label}: {mr['title']} ===")
            review_and_comment(
                client,
                target_identifier,
                mr["iid"],
                model=args.model,
                dry_run=args.dry_run,
                provider=provider,
            )
        return

if __name__ == "__main__":
    main()
