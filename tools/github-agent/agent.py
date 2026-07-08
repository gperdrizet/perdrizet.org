"""GitHub Agent: syncs public GitHub repos into user content files.

Rules:
    - A raw snapshot is written to data/user/projects.raw.yaml on every sync.
    - Curated projects in data/user/projects.yaml are never overwritten.
    - Only new repos/groups are appended to curated projects.
    - description_short is generated via LLM when LLM_API_KEY is set.
    - Forked repos are skipped by default (pass --include-forks to override).

Usage:
    python agent.py [--bootstrap] [--include-forks] [--dry-run]

Profile is read from data/user/profile.yaml.
Runtime settings are read from data/user/runtime.json.
LLM API key is read from the LLM_API_KEY environment variable.
Optional GitHub token: GITHUB_TOKEN env var (raises rate limit from 60 to 5000 req/hr).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from openai import OpenAI
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

CONTENT_ROOT = REPO_ROOT / "data" / "user"
PROFILE_PATH = CONTENT_ROOT / "profile.yaml"
LEGACY_PROFILE_PATH = CONTENT_ROOT / "config.yaml"
PROJECTS_PATH = CONTENT_ROOT / "projects.yaml"
RAW_PROJECTS_PATH = CONTENT_ROOT / "projects.raw.yaml"
RUNTIME_PATH = CONTENT_ROOT / "runtime.json"

# ---------------------------------------------------------------------------
# Load .env from repo root (if present)
# ---------------------------------------------------------------------------

load_dotenv(REPO_ROOT / ".env", override=False)

# ---------------------------------------------------------------------------
# Profile/runtime loading
# ---------------------------------------------------------------------------

def _build_default_profile() -> dict[str, Any] | None:
    username = os.environ.get("GITHUB_USERNAME", "").strip()
    if not username:
        return None

    display_name = os.environ.get("PERSONAL_NAME", "").strip() or username
    email = os.environ.get("PERSONAL_EMAIL", "").strip()
    domain = os.environ.get("PERSONAL_DOMAIN", "").strip()

    return {
        "personal": {
            "name": display_name,
            "tagline": "Builder • Engineer • Educator",
            "domain": domain,
            "email": email,
            "github_username": username,
            "social": {
                "linkedin": "",
                "twitter": "",
                "bluesky": "",
                "substack": "",
                "github": f"https://github.com/{username}",
            },
        },
        "bio": {
            "short": "",
            "long": "",
        },
        "home_sections": [],
        "about_sections": [],
    }


def _write_yaml(path: Path, data: dict) -> None:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 120
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def ensure_user_profile() -> bool:
    """Create data/user/profile.yaml from env values if it does not exist."""
    if PROFILE_PATH.exists():
        return True
    default_profile = _build_default_profile()
    if not default_profile:
        return False
    _write_yaml(PROFILE_PATH, default_profile)
    print(f"Bootstrapped {PROFILE_PATH.relative_to(REPO_ROOT)} from environment")
    return True


def load_profile(allow_bootstrap: bool = False) -> dict:
    if not PROFILE_PATH.exists() and LEGACY_PROFILE_PATH.exists():
        safe_yaml = YAML(typ="safe")
        with open(LEGACY_PROFILE_PATH, encoding="utf-8") as f:
            legacy = safe_yaml.load(f)
        if isinstance(legacy, dict):
            _write_yaml(PROFILE_PATH, legacy)

    if not PROFILE_PATH.exists():
        if not allow_bootstrap or not ensure_user_profile():
            raise FileNotFoundError(
                f"Missing required profile file: {PROFILE_PATH}. "
                "Set GITHUB_USERNAME in .env and pass --bootstrap to initialize user content."
            )
    safe_yaml = YAML(typ="safe")
    with open(PROFILE_PATH, encoding="utf-8") as f:
        data = safe_yaml.load(f)
    return data or {}


def load_runtime() -> dict[str, Any]:
    if not RUNTIME_PATH.exists():
        return {}
    try:
        data = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"


def _normalize_token(token: str | None) -> str:
    return (token or "").strip()


def fetch_repos(username: str, token: str | None, include_forks: bool) -> list[dict]:
    """Return all public repos for the given GitHub username."""
    token = _normalize_token(token)

    def _headers(use_auth: bool) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if use_auth and token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # If a provided token is invalid/revoked, fall back to public API mode
    # so bootstrap can still complete for public repositories.
    use_auth = bool(token)
    while True:
        repos = []
        page = 1
        retry_without_auth = False
        while True:
            resp = requests.get(
                f"{GITHUB_API}/users/{username}/repos",
                headers=_headers(use_auth),
                params={"per_page": 100, "page": page, "type": "public"},
                timeout=30,
            )
            if resp.status_code == 401 and use_auth:
                print("[warn] GitHub token rejected; retrying without token", file=sys.stderr)
                use_auth = False
                retry_without_auth = True
                break
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            repos.extend(batch)
            page += 1
        if retry_without_auth:
            continue
        if repos or page >= 1:
            break

    if not include_forks:
        repos = [r for r in repos if not r.get("fork")]

    return repos


def fetch_repo_languages(repo: dict, token: str | None) -> list[str]:
    """Return the top languages for a repo, ordered by bytes."""
    token = _normalize_token(token)
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(repo["languages_url"], headers=headers, timeout=15)
        resp.raise_for_status()
        langs = resp.json()
        return [k.lower() for k in sorted(langs, key=langs.get, reverse=True)][:5]
    except Exception:
        return []


def fetch_repo_topics(repo: dict, token: str | None) -> list[str]:
    """Return GitHub topics for a repo."""
    token = _normalize_token(token)
    headers = {
        "Accept": "application/vnd.github.mercy-preview+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo['full_name']}/topics",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("names", [])
    except Exception:
        return []


def fetch_pinned_repo_names(username: str, token: str | None) -> set[str]:
    """Return pinned repository names for the user via GitHub GraphQL."""
    token = _normalize_token(token)
    if not token:
        return set()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    query = """
        query($login: String!) {
            user(login: $login) {
                pinnedItems(first: 6, types: REPOSITORY) {
                    nodes {
                        ... on Repository {
                            name
                        }
                    }
                }
            }
        }
        """

    try:
        resp = requests.post(
            f"{GITHUB_API}/graphql",
            headers=headers,
            json={"query": query, "variables": {"login": username}},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        nodes = (((payload.get("data") or {}).get("user") or {}).get("pinnedItems") or {}).get("nodes") or []
        names = {n.get("name", "").strip() for n in nodes if isinstance(n, dict)}
        return {name for name in names if name}
    except Exception as exc:
        print(f"[warn] Could not fetch pinned repos: {exc}", file=sys.stderr)
        return set()


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

DESCRIPTION_PROMPT = """\
You are writing a concise project description for a personal portfolio website.

Project name: {name}
GitHub description: {github_description}
Primary languages: {languages}
Topics/tags: {topics}

Write 1-2 sentences (max 50 words) describing what this project does and why it is \
interesting. Be specific and technical. Do not start with "This project" or "A project". \
Do not use markdown. Output only the description text."""


SUGGEST_GROUPS_PROMPT = """\
You are helping organize a software engineer's GitHub repositories into logical \
groups for a portfolio website. Each group will appear as a single consolidated \
project page rather than many individual pages.

Here are the repositories (name | description | languages | topics):
{repo_list}

Suggest groups that make thematic sense: same domain, related techniques, a \
course/series, a shared technology stack, etc. Only suggest a group if 3 or more \
repos clearly belong together. Skip repos that stand alone as unique projects.

Output ONLY a valid YAML snippet in exactly this format, ready to paste into \
runtime.json under sync.groups. Use the `name` field as a slug (lowercase, \
hyphens). Do not output any explanation or markdown fences.

Example format:
  - name: devcontainers
    display_name: "Development Container Templates"
    description_short: >
      Containerized development environments for Python data science, deep learning,
      and LLM projects.
    tags: [docker, devcontainer, python]
    roles: [developer]
    status: active
    featured: false
    repos:
      - deeplearning-devcontainer
      - datascience-devcontainer
      - llms-devcontainer"""


def generate_description(client: OpenAI, model: str, repo: dict, languages: list[str], topics: list[str]) -> str:
    prompt = DESCRIPTION_PROMPT.format(
        name=repo["name"],
        github_description=repo.get("description") or "(none)",
        languages=", ".join(languages) or "(unknown)",
        topics=", ".join(topics) or "(none)",
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        print(f"  [warn] LLM call failed for {repo['name']}: {exc}", file=sys.stderr)
        return repo.get("description") or ""


def suggest_groups(client: OpenAI, model: str, repos: list[dict]) -> str:
    """Ask the LLM to suggest group consolidations for the given repo list."""
    lines = []
    for r in repos:
        desc = (r.get("description") or "").replace("\n", " ")[:120]
        langs = ", ".join(r.get("_languages", []))
        topics = ", ".join(r.get("_topics", []))
        lines.append(f"{r['name']} | {desc} | {langs} | {topics}")
    repo_list = "\n".join(lines)

    prompt = SUGGEST_GROUPS_PROMPT.format(repo_list=repo_list)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[error] LLM call failed: {exc}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def load_projects_yaml(path: Path, create_if_missing: bool = False) -> tuple[YAML, dict]:
    """Load a projects-style YAML file preserving comments."""
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 120
    if not path.exists():
        if not create_if_missing:
            raise FileNotFoundError(f"Missing required projects file: {path}")
        data = {"projects": []}
    else:
        with open(path, encoding="utf-8") as f:
            data = ryaml.load(f)
        if data is None:
            data = {"projects": []}
    return ryaml, data


def existing_slugs(data: dict) -> set[str]:
    projects = data.get("projects")
    if not isinstance(projects, list):
        return set()
    slugs: set[str] = set()
    for project in projects:
        if isinstance(project, dict):
            name = project.get("name")
            if isinstance(name, str) and name.strip():
                slugs.add(name)
    return slugs


def make_entry(repo: dict, languages: list[str], topics: list[str], description: str) -> dict:
    """Build a new projects.yaml entry from a GitHub repo."""
    tags = list(dict.fromkeys(topics + languages))  # topics first, deduped

    # Infer status from repo state
    if repo.get("archived"):
        status = "archived"
    elif repo.get("topics") and any(t in ("wip", "in-progress") for t in topics):
        status = "wip"
    else:
        status = "published"

    return {
        "name": repo["name"],
        "display_name": repo["name"].replace("-", " ").replace("_", " ").title(),
        "status": status,
        "featured": False,
        "tags": tags,
        "roles": [],
        "github": repo["html_url"],
        "service_url": repo.get("homepage") or "",
        "package_url": "",
        "description_short": LiteralScalarString(description + "\n") if description else "",
        "description_long": "",
        "teaching_context": "",
        "highlights": [],
    }


def append_entry(data: dict, entry: dict) -> None:
    if data.get("projects") is None:
        data["projects"] = []
    data["projects"].append(entry)


def make_group_entry(group: dict, owner: str) -> dict:
    """Build a collection entry for a group of repos."""
    description = group.get("description_short", "").strip()
    members = [{"repo": f"{owner}/{repo_name}"} for repo_name in group.get("repos", [])]
    return {
        "kind": "collection",
        "type": "group",
        "name": group["name"],
        "display_name": group["display_name"],
        "featured": group.get("featured", False),
        "tags": group.get("tags", []),
        "roles": group.get("roles", []),
        "summary": LiteralScalarString(description + "\n") if description else "",
        "members": members,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync GitHub repos to the active projects file")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Create data/user/profile.yaml from env if missing (requires GITHUB_USERNAME)",
    )
    parser.add_argument("--include-forks", action="store_true", help="Include forked repos")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    parser.add_argument("--min-stars", type=int, default=0, help="Skip repos with fewer than N stars")
    parser.add_argument("--suggest-groups", action="store_true",
                        help="Ask the LLM to suggest group consolidations and print YAML to stdout")
    parser.add_argument(
        "--seed-from-pins",
        action="store_true",
        help="When bootstraping a fresh curated projects file, seed from GitHub profile pins only",
    )
    args = parser.parse_args()

    profile = load_profile(allow_bootstrap=args.bootstrap)
    runtime = load_runtime()

    username: str = ((runtime.get("github") or {}).get("username") or "").strip()
    if not username:
        username = ((profile.get("personal") or {}).get("github_username") or "").strip()
    if not username:
        username = os.environ.get("GITHUB_USERNAME", "").strip()
    if not username:
        print(
            "[error] Missing GitHub username. Set github.username in "
            f"{RUNTIME_PATH.relative_to(REPO_ROOT)} or GITHUB_USERNAME in .env.",
            file=sys.stderr,
        )
        sys.exit(1)
    llm_cfg = runtime.get("llm") if isinstance(runtime.get("llm"), dict) else {}

    token = os.environ.get("GITHUB_TOKEN")
    api_key = os.environ.get("LLM_API_KEY")

    # LLM client, only if key is available
    llm_client: OpenAI | None = None
    if api_key:
        llm_client = OpenAI(
            api_key=api_key,
            base_url=llm_cfg.get("base_url", "http://localhost:8080/v1"),
        )
        print(f"LLM: {llm_cfg.get('model')} @ {llm_cfg.get('base_url')}")
    else:
        print("LLM_API_KEY not set; descriptions will use GitHub description as fallback.")

    sync_cfg = runtime.get("sync") if isinstance(runtime.get("sync"), dict) else {}
    skip_list: list[str] = sync_cfg.get("skip", []) if isinstance(sync_cfg.get("skip", []), list) else []
    groups: list[dict] = sync_cfg.get("groups", []) if isinstance(sync_cfg.get("groups", []), list) else []
    # Build a flat set of all repo names that belong to any group
    grouped_repos: set[str] = {r for g in groups for r in g.get("repos", [])}

    print(f"Fetching public repos for github.com/{username}...")
    repos = fetch_repos(username, token, args.include_forks)
    print(f"  Found {len(repos)} repo(s)")

    # --- suggest-groups mode: fetch metadata, ask LLM, print YAML, exit ---
    if args.suggest_groups:
        if not llm_client:
            print("[error] --suggest-groups requires LLM_API_KEY to be set.", file=sys.stderr)
            sys.exit(1)
        # Exclude already-grouped and skipped repos; fetch their metadata
        candidate_repos = [
            r for r in repos
            if r["name"] not in skip_list and r["name"] not in grouped_repos
        ]
        print(f"  Fetching metadata for {len(candidate_repos)} candidate repo(s)...")
        for repo in candidate_repos:
            repo["_languages"] = fetch_repo_languages(repo, token)
            repo["_topics"] = fetch_repo_topics(repo, token)
        print("  Asking LLM for group suggestions...\n")
        yaml_snippet = suggest_groups(llm_client, llm_cfg.get("model", ""), candidate_repos)
        print(f"# ---- Suggested groups (paste into {RUNTIME_PATH.relative_to(REPO_ROOT)} under sync.groups) ----")
        print(yaml_snippet)
        return

    if skip_list:
        repos = [r for r in repos if r["name"] not in skip_list]
        print(f"  {len(repos)} after applying skip list")

    if grouped_repos:
        print(f"  {len(grouped_repos)} repo(s) are referenced by configured groups")

    if args.min_stars > 0:
        repos = [r for r in repos if (r.get("stargazers_count") or 0) >= args.min_stars]
        print(f"  {len(repos)} with >= {args.min_stars} star(s)")

    ryaml, data = load_projects_yaml(PROJECTS_PATH, create_if_missing=True)
    known = existing_slugs(data)

    pinned_names: set[str] | None = None
    seed_mode = args.seed_from_pins and len(known) == 0
    if seed_mode:
        pinned_names = fetch_pinned_repo_names(username, token)
        if pinned_names:
            print(f"  Seeding curated projects from {len(pinned_names)} pinned repo(s)")
        else:
            print(
                "[error] No pinned repos found (or token invalid/missing). "
                "Bootstrap requires pinned repos for initial curated seed.",
                file=sys.stderr,
            )
            sys.exit(2)

    raw_entries = []
    for repo in repos:
        languages = fetch_repo_languages(repo, token)
        topics = fetch_repo_topics(repo, token)

        use_llm = bool(llm_client)
        if seed_mode and pinned_names is not None and repo["name"] not in pinned_names:
            use_llm = False

        if use_llm:
            description = generate_description(llm_client, llm_cfg.get("model", ""), repo, languages, topics)
        else:
            description = repo.get("description") or ""

        entry = make_entry(repo, languages, topics, description)
        raw_entries.append(entry)

    raw_data = {"projects": raw_entries}
    if args.dry_run:
        print(
            f"\n[dry-run] would write raw snapshot with {len(raw_entries)} repo(s) "
            f"to {RAW_PROJECTS_PATH.relative_to(REPO_ROOT)}"
        )
    else:
        _write_yaml(RAW_PROJECTS_PATH, raw_data)
        print(f"Wrote raw snapshot: {RAW_PROJECTS_PATH.relative_to(REPO_ROOT)} ({len(raw_entries)} repo(s))")

    new_repo_entries = [entry for entry in raw_entries if entry["name"] not in known]
    if seed_mode and pinned_names is not None:
        new_repo_entries = [entry for entry in new_repo_entries if entry["name"] in pinned_names]
        for entry in new_repo_entries:
            entry["featured"] = True

    new_groups = [g for g in groups if g["name"] not in known]
    print(
        f"  {len(known)} already curated, {len(new_repo_entries)} new repo(s), "
        f"{len(new_groups)} new group(s)"
    )

    if not new_repo_entries and not new_groups:
        print("No curated additions needed.")
        return

    added: list[str] = []

    for group in new_groups:
        print(f"\n  [group] {group['name']}")
        entry = make_group_entry(group, username)

        if args.dry_run:
            print(f"    [dry-run] would add group: {entry['display_name']}")
            print(f"    repos: {', '.join(group.get('repos', []))}")
        else:
            append_entry(data, entry)
            added.append(group["name"])

    for entry in new_repo_entries:
        print(f"\n  + {entry['name']}")
        if args.dry_run:
            print(f"    [dry-run] would add: {entry['display_name']}")
            short_desc = str(entry.get("description_short") or "")
            print(f"    description: {short_desc[:80]}...")
        else:
            append_entry(data, entry)
            added.append(entry["name"])

    if not args.dry_run and added:
        with open(PROJECTS_PATH, "w", encoding="utf-8") as f:
            ryaml.dump(data, f)
        print(f"\nWrote {len(added)} new curated entry(s) to {PROJECTS_PATH.relative_to(REPO_ROOT)}")
        print("Review and fill in: roles, featured, highlights, teaching_context")
    elif args.dry_run:
        total = len(new_repo_entries) + len(new_groups)
        print(
            f"\n[dry-run] would have added {total} entry(s) "
            f"({len(new_groups)} consolidated group(s), {len(new_repo_entries)} individual repo(s))"
        )


if __name__ == "__main__":
    main()
