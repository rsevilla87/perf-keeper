"""GitHub pull request metadata via the REST API (not HTML PR pages)."""
from __future__ import annotations

import logging
import re

import httpx
from langchain_core.tools import tool

from perf_keeper.config import get_config

logger = logging.getLogger(__name__)

_GITHUB_PULL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:/|$|\?)",
    re.IGNORECASE,
)

_GITHUB_COMMIT_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/commit/(?P<sha>[0-9a-f]+)(?:/|$|\?)?",
    re.IGNORECASE,
)


def _parse_pr_url(pr_url: str) -> tuple[str, str, str] | None:
    pr_url = pr_url.strip()
    m = _GITHUB_PULL_RE.match(pr_url)
    if not m:
        return None
    return m.group("owner"), m.group("repo"), m.group("number")


def _parse_commit_url(commit_url: str) -> tuple[str, str, str] | None:
    commit_url = commit_url.strip()
    m = _GITHUB_COMMIT_RE.match(commit_url)
    if not m:
        return None
    return m.group("owner"), m.group("repo"), m.group("sha")


def _github_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = get_config().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _format_pr_response(data: dict) -> str:
    body = (data.get("body") or "").strip()
    max_body = 12_000
    if len(body) > max_body:
        body = body[:max_body] + "\n\n...[body truncated]"
    labels = [lb.get("name", "") for lb in (data.get("labels") or []) if isinstance(lb, dict)]
    lines = [
        f"title: {data.get('title')}",
        f"state: {data.get('state')} merged: {data.get('merged')}",
        f"author: {(data.get('user') or {}).get('login')}",
        f"html_url: {data.get('html_url')}",
        f"labels: {', '.join(labels) if labels else '(none)'}",
        "",
        "body:",
        body or "(empty)",
    ]
    return "\n".join(lines)


@tool
def fetch_github_pull_request(pr_url: str) -> str:
    """Fetch a GitHub pull request title, body, labels, and state via the REST API.

    Use this for ``https://github.com/<owner>/<repo>/pull/<number>`` URLs (for example
    from Orion "Related PRs"). Do **not** use ``fetch_artifact`` for GitHub PR pages;
    those return HTML. Pass the same browser URL here.

    Args:
        pr_url: Full GitHub pull request URL, optionally with trailing path segments.
    """
    logger.info(f"Fetching GitHub pull request {pr_url}")
    parsed = _parse_pr_url(pr_url)
    if not parsed:
        return (
            "Invalid or unsupported GitHub PR URL. Expected "
            "https://github.com/<owner>/<repo>/pull/<number>"
        )
    owner, repo, number = parsed
    api_base = get_config().github_api_url.rstrip("/")
    url = f"{api_base}/repos/{owner}/{repo}/pulls/{number}"
    try:
        resp = httpx.get(url, headers=_github_headers(), follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        return _format_pr_response(resp.json())
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            return "GitHub API: pull request not found (404)."
        if e.response is not None and e.response.status_code in (401, 403):
            return (
                "GitHub API: access denied. Set GITHUB_TOKEN in the environment "
                f"for private repos or rate limits. ({e.response.status_code})"
            )
        return f"GitHub API error: {e}"
    except Exception as e:
        return f"Error fetching GitHub pull request: {e}"


def _get_all_pr_commits(owner: str, repo: str, number: str) -> list[dict]:
    """Fetch all commits for a PR, paginating through all pages (GitHub max: 250)."""
    api_base = get_config().github_api_url.rstrip("/")
    commits: list[dict] = []
    page = 1
    logger.info(f"Fetching all commits for PR {number} in {owner}/{repo}")
    while True:
        url = f"{api_base}/repos/{owner}/{repo}/pulls/{number}/commits?per_page=100&page={page}"
        resp = httpx.get(url, headers=_github_headers(), follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return commits


def _format_commit_row(commit: dict, owner: str, repo: str) -> str:
    sha = commit.get("sha", "")
    short_sha = sha[:7]
    c = commit.get("commit", {})
    message = (c.get("message") or "").splitlines()[0][:120]
    author = (c.get("author") or {}).get("name", "unknown")
    date = ((c.get("author") or {}).get("date") or "")[:10]
    commit_url = f"https://github.com/{owner}/{repo}/commit/{sha}"
    return f"{short_sha} | {date} | {author} | {message}\n  commit URL: {commit_url}"


@tool
def fetch_pr_commits(pr_url: str) -> str:
    """Fetch the list of commits in a GitHub pull request (metadata only — no diffs).

    Returns one line per commit: short SHA, date, author, and first line of the commit
    message, plus the full commit URL for use with fetch_commit_files. Use this tool
    on large downstream merge or branch-sync PRs to identify candidate culprit commits
    from their messages before inspecting individual file changes.

    Args:
        pr_url: Full GitHub pull request URL, e.g. https://github.com/<owner>/<repo>/pull/<number>
    """
    parsed = _parse_pr_url(pr_url)
    if not parsed:
        return (
            "Invalid or unsupported GitHub PR URL. Expected "
            "https://github.com/<owner>/<repo>/pull/<number>"
        )
    owner, repo, number = parsed
    try:
        commits = _get_all_pr_commits(owner, repo, number)
        if not commits:
            return "No commits found for this pull request."
        lines = [f"Total commits: {len(commits)}", ""]
        lines.append("sha7    | date       | author                  | message")
        lines.append("--------|------------|-------------------------|--------")
        for commit in commits:
            lines.append(_format_commit_row(commit, owner, repo))
        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            logger.error(f"GitHub API: pull request not found (404). {pr_url}")
            return "GitHub API: pull request not found (404)."
        if e.response is not None and e.response.status_code in (401, 403):
            logger.error(f"GitHub API: access denied. {pr_url}")
            return (
                "GitHub API: access denied. Set GITHUB_TOKEN in the environment "
                f"for private repos or rate limits. ({e.response.status_code})"
            )
        return f"GitHub API error: {e}"
    except Exception as e:
        logger.error(f"Error fetching PR commits: {e}")
        return f"Error fetching PR commits: {e}"


@tool
def fetch_commit_files(commit_url: str) -> str:
    """Fetch the list of files changed in a GitHub commit (filenames and line counts only — no patch text).

    Returns the commit message, author, date, and a table of changed files with their
    status (added/modified/removed) and line counts. Use this after fetch_pr_commits to
    confirm which candidate commits touch files relevant to the regressing component.

    Args:
        commit_url: Full GitHub commit URL, e.g. https://github.com/<owner>/<repo>/commit/<sha>
    """
    logger.info(f"Fetching commit files for {commit_url}")
    parsed = _parse_commit_url(commit_url)
    if not parsed:
        return (
            "Invalid or unsupported GitHub commit URL. Expected "
            "https://github.com/<owner>/<repo>/commit/<sha>"
        )
    owner, repo, sha = parsed
    api_base = get_config().github_api_url.rstrip("/")
    url = f"{api_base}/repos/{owner}/{repo}/commits/{sha}"
    try:
        resp = httpx.get(url, headers=_github_headers(), follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        data = resp.json()
        c = data.get("commit", {})
        message = (c.get("message") or "").splitlines()[0]
        author = (c.get("author") or {}).get("name", "unknown")
        date = ((c.get("author") or {}).get("date") or "")[:10]
        files = data.get("files", [])
        lines = [
            f"sha: {sha}",
            f"message: {message}",
            f"author: {author}  date: {date}",
            "",
        ]
        cap = 100
        truncated = len(files) > cap
        shown = files[:cap]
        lines.append(f"Files changed ({len(files)}){' — showing first 100' if truncated else ''}:")
        for f in shown:
            status = f.get("status", "modified")
            filename = f.get("filename", "")
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            lines.append(f"  {status:<10} {filename}  +{additions} -{deletions}")
        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            logger.error(f"GitHub API: commit not found (404). {commit_url}")
            return "GitHub API: commit not found (404)."
        if e.response is not None and e.response.status_code in (401, 403):
            logger.error(f"GitHub API: access denied. {commit_url}")
            return (
                "GitHub API: access denied. Set GITHUB_TOKEN in the environment "
                f"for private repos or rate limits. ({e.response.status_code})"
            )
        return f"GitHub API error: {e}"
    except Exception as e:
        return f"Error fetching commit files: {e}"


@tool
def fetch_commit_url(commit_url: str) -> str:
    """Fetch the URL of a GitHub commit content."""
    parsed = _parse_commit_url(commit_url)
    if not parsed:
        return (
            "Invalid or unsupported GitHub commit URL. Expected "
            "https://github.com/<owner>/<repo>/commit/<sha>"
        )
    owner, repo, sha = parsed
    api_base = get_config().github_api_url.rstrip("/")
    url = f"{api_base}/repos/{owner}/{repo}/commits/{sha}.patch"
    try:
        resp = httpx.get(url, headers=_github_headers(), follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            logger.error(f"GitHub API: commit not found (404). {commit_url}")
            return "GitHub API: commit not found (404)."
        if e.response is not None and e.response.status_code in (401, 403):
            logger.error(f"GitHub API: access denied. {commit_url}")
            return (
                "GitHub API: access denied. Set GITHUB_TOKEN in the environment "
                f"for private repos or rate limits. ({e.response.status_code})"
            )
    return commit_url