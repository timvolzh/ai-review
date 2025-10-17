"""Prompt templates for the review workflow."""
from __future__ import annotations

import textwrap

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

SUMMARY_PROMPT_TEMPLATE = textwrap.dedent(
    """
    Based on the following per-chunk reviews, write a concise overall summary with priority labels:
    - MUST FIX (blocking)
    - SHOULD FIX (important)
    - NICE TO HAVE (non-blocking)

    Keep it under 200 words.

    Reviews:
    {reviews}
    """
).strip()
