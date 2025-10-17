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
from vcs_client import GitLabClient, GitLabError

# -----------------------------
# Environment & Configuration
# -----------------------------

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

GITLAB_URL = os.getenv("GITLAB_URL", "").rstrip("/")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
PROJECT_ID = os.getenv("GITLAB_PROJECT_ID")
VERIFY_GITLAB_SSL = os.getenv("GITLAB_VERIFY_SSL", "false").lower() in {
    "1",
    "true",
    "yes",
}

_gitlab_client: Optional[GitLabClient] = None


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
            requests.packages.urllib3.disable_warnings(  # type: ignore[attr-defined]
                category=requests.packages.urllib3.exceptions.InsecureRequestWarning  # type: ignore[attr-defined]
            )
            print(
                "⚠️  SSL verification is DISABLED — use only in trusted environments.",
                file=sys.stderr,
            )
        _gitlab_client = client
    return _gitlab_client

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

def build_review_for_mr(mr: dict, changes: List[dict], model: Optional[str] = None) -> str:
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
    header_block = textwrap.dedent(f"""
        # 🤖 AI Code Review (model: {model or OLLAMA_MODEL})
        _This is an automated review. Please verify suggestions before applying._

        **MR:** !{mr.get('iid')} — **{title}**  
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
    review_md = build_review_for_mr(mr, changes, model=model)
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

# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AI MR Reviewer for GitLab using Ollama (SSL disabled)")
    g_target = p.add_mutually_exclusive_group(required=True)
    g_target.add_argument("--iid", type=int, help="Review a specific MR by IID")
    g_target.add_argument("--list", action="store_true", help="List open MRs and exit")
    g_target.add_argument("--all", action="store_true", help="Review all open MRs")
    p.add_argument("--model", default=OLLAMA_MODEL, help=f"Ollama model to use (default: {OLLAMA_MODEL})")
    p.add_argument("--dry-run", action="store_true", help="Don't post to GitLab; print a preview")
    return p.parse_args()

def main() -> None:
    args = parse_args()
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
            print(f"!{mr['iid']}: {mr['title']} (by {mr['author']['name']}) — {mr['web_url']}")
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
        review_and_comment(client, PROJECT_ID, args.iid, model=args.model, dry_run=args.dry_run)
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
            review_and_comment(client, PROJECT_ID, mr['iid'], model=args.model, dry_run=args.dry_run)
        return

if __name__ == "__main__":
    main()
