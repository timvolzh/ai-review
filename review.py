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
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

# -----------------------------
# Environment & Configuration
# -----------------------------

load_dotenv()

GITLAB_URL = os.getenv("GITLAB_URL", "").rstrip("/")
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
PROJECT_ID = os.getenv("GITLAB_PROJECT_ID")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

if not (GITLAB_URL and GITLAB_TOKEN and PROJECT_ID):
    missing = [k for k, v in {
        'GITLAB_URL': GITLAB_URL,
        'GITLAB_TOKEN': GITLAB_TOKEN,
        'GITLAB_PROJECT_ID': PROJECT_ID,
    }.items() if not v]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        print("Please set them or create a .env file.")

HEADERS = {
    "PRIVATE-TOKEN": GITLAB_TOKEN or "",
    "Content-Type": "application/json",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.verify = False
requests.packages.urllib3.disable_warnings(category=requests.packages.urllib3.exceptions.InsecureRequestWarning)
print("⚠️  SSL verification is DISABLED — use only in trusted environments.", file=sys.stderr)

# -----------------------------
# Helpers
# -----------------------------

class GitLabError(RuntimeError):
    pass

@retry(reraise=True, stop=stop_after_attempt(4), wait=wait_exponential(multiplier=0.5, min=0.5, max=6), retry=retry_if_exception_type(requests.RequestException))
def _gl_get(path: str, params: Optional[dict] = None) -> requests.Response:
    if not GITLAB_URL:
        raise GitLabError("GITLAB_URL is not configured")
    url = f"{GITLAB_URL}/api/v4{path}"
    resp = SESSION.get(url, params=params, timeout=30)
    if resp.status_code >= 400:
        raise GitLabError(f"GET {url} failed: {resp.status_code} {resp.text[:200]}")
    return resp

@retry(reraise=True, stop=stop_after_attempt(4), wait=wait_exponential(multiplier=0.5, min=0.5, max=6), retry=retry_if_exception_type(requests.RequestException))
def _gl_post(path: str, json: dict) -> requests.Response:
    if not GITLAB_URL:
        raise GitLabError("GITLAB_URL is not configured")
    url = f"{GITLAB_URL}/api/v4{path}"
    resp = SESSION.post(url, json=json, timeout=30)
    if resp.status_code >= 400:
        raise GitLabError(f"POST {url} failed: {resp.status_code} {resp.text[:200]}")
    return resp

# -----------------------------
# GitLab API wrappers
# -----------------------------

def list_open_merge_requests(project_id: str, per_page: int = 50) -> List[dict]:
    mrs: List[dict] = []
    page = 1
    while True:
        resp = _gl_get(f"/projects/{project_id}/merge_requests", params={"state": "opened", "per_page": per_page, "page": page, "order_by": "updated_at", "sort": "desc"})
        batch = resp.json()
        if not batch:
            break
        mrs.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return mrs

def get_merge_request(project_id: str, iid: int) -> dict:
    return _gl_get(f"/projects/{project_id}/merge_requests/{iid}").json()

def get_merge_request_changes(project_id: str, iid: int) -> dict:
    return _gl_get(f"/projects/{project_id}/merge_requests/{iid}/changes").json()

def post_merge_request_note(project_id: str, iid: int, body_markdown: str) -> dict:
    return _gl_post(f"/projects/{project_id}/merge_requests/{iid}/notes", json={"body": body_markdown}).json()

# -----------------------------
# Ollama client
# -----------------------------

class OllamaError(RuntimeError):
    pass

@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=4), retry=retry_if_exception_type(requests.RequestException))
def ollama_generate(prompt: str, model: Optional[str] = None, temperature: float = 0.2, system: Optional[str] = None) -> str:
    model = model or OLLAMA_MODEL
    url = f"{OLLAMA_URL}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": temperature}}
    if system:
        payload["system"] = system
    resp = requests.post(url, json=payload, timeout=120, verify=False)
    if resp.status_code >= 400:
        raise OllamaError(f"Ollama error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data.get("response", "").strip()

# -----------------------------
# Review pipeline
# -----------------------------

MAX_CHARS_PER_CHUNK = 8000
MAX_NOTE_SIZE = 10000

REVIEW_SYSTEM_PROMPT = (
    "You are a senior software engineer performing a thorough code review on a Git diff. "
    "Be pragmatic: focus on correctness, security, readability, performance, and maintainability. "
    "Point out broken tests, edge cases, anti-patterns, concurrency issues, error handling, logging, and docs. "
    "If you propose code changes, format them using GitLab suggestion blocks where possible."
)

REVIEW_USER_PROMPT_TEMPLATE = textwrap.dedent(
    """
    Merge Request: {title}
    Author: {author}
    Description:\n{description}

    Review the following Git diff chunk. For each file, list findings as bullets under a `### <file>` heading.
    Be concise but specific. Use examples. Use GitLab suggestion blocks for small fixes:

    ```suggestion
    // new code here
    ```

    Diff chunk:
    ```diff
    {diff_text}
    ```
    """
).strip()

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
        user_prompt = REVIEW_USER_PROMPT_TEMPLATE.format(title=title, author=author, description=description, diff_text=chunk_text)
        try:
            section = ollama_generate(user_prompt, model=model, system=REVIEW_SYSTEM_PROMPT)
        except Exception as e:
            section = f"(Error generating review for chunk {idx}: {e})"
        header = f"## Review chunk {idx}/{len(chunks)} ({', '.join(paths)})"
        all_sections.append(f"{header}\n\n{section}\n")
        time.sleep(0.2)
    summary_prompt = textwrap.dedent(f"""
        Based on the following per-chunk reviews, write a concise overall summary with priority labels:
        - MUST FIX (blocking)
        - SHOULD FIX (important)
        - NICE TO HAVE (non-blocking)

        Keep it under 200 words.

        Reviews:
        {"\n\n".join(all_sections)[:6000]}
    """)
    try:
        summary = ollama_generate(summary_prompt, model=model, system=REVIEW_SYSTEM_PROMPT)
    except Exception as e:
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

def review_and_comment(project_id: str, iid: int, model: Optional[str] = None, dry_run: bool = False) -> None:
    mr = get_merge_request(project_id, iid)
    changes_doc = get_merge_request_changes(project_id, iid)
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
        resp = post_merge_request_note(project_id, iid, prefix + note)
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
        mrs = list_open_merge_requests(PROJECT_ID)
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
        review_and_comment(PROJECT_ID, args.iid, model=args.model, dry_run=args.dry_run)
        return
    if args.all:
        if not (PROJECT_ID and GITLAB_URL and GITLAB_TOKEN):
            print("Set GITLAB_URL, GITLAB_TOKEN, and GITLAB_PROJECT_ID to review MRs.")
            sys.exit(2)
        mrs = list_open_merge_requests(PROJECT_ID)
        for mr in mrs:
            print(f"\n=== Reviewing MR !{mr['iid']}: {mr['title']} ===")
            review_and_comment(PROJECT_ID, mr['iid'], model=args.model, dry_run=args.dry_run)
        return

if __name__ == "__main__":
    main()
