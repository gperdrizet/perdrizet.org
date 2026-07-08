"""GitHub Agent: syncs public GitHub repos into the active projects file.

Rules:
  - Repos already in projects.yaml are never modified.
  - New repos are appended with a skeleton entry.
  - description_short is generated via LLM for new repos if LLM_API_KEY is set.
  - Forked repos are skipped by default (pass --include-forks to override).

Usage:
  python agent.py [--include-forks] [--dry-run]

Configuration is read from the active config file.
LLM API key is read from the LLM_API_KEY environment variable.
Optional GitHub token: GITHUB_TOKEN env var (raises rate limit from 60 to 5000 req/hr).
"""

import argparse
import os
import sys
from pathlib import Path

import requests
from openai import OpenAI
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

# Check if the user has data
if Path(REPO_ROOT / "data" / "user" / "config.yaml").exists() and \
   Path(REPO_ROOT / "data" / "user" / "projects.yaml").exists():
    CONTENT_ROOT = REPO_ROOT / "data" / "user"

else:
    # fallback for placeholder site data
    CONTENT_ROOT = REPO_ROOT / "data"

CONFIG_PATH = CONTENT_ROOT / "config.yaml"
PROJECTS_PATH = CONTENT_ROOT / "projects.yaml"

# ---------------------------------------------------------------------------
# Load .env from repo root (if present) without requiring python-dotenv
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path) -> None:
    """Parse a .env file and set missing environment variables."""
    if not path.is_file():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing required config file: {CONFIG_PATH}")
    safe_yaml = YAML(typ="safe")
    with open(CONFIG_PATH) as f:
        return safe_yaml.load(f)


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"


def fetch_repos(username: str, token: str | None, include_forks: bool) -> list[dict]:
    """Return all public repos for the given GitHub username."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repos = []
    page = 1
    while True:
        resp = requests.get(
            f"{GITHUB_API}/users/{username}/repos",
            headers=headers,
            params={"per_page": 100, "page": page, "type": "public"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1

    if not include_forks:
        repos = [r for r in repos if not r.get("fork")]

    return repos


def fetch_repo_languages(repo: dict, token: str | None) -> list[str]:
    """Return the top languages for a repo, ordered by bytes."""
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
config.yaml under github_agent.groups. Use the `name` field as a slug (lowercase, \
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

def load_projects_yaml() -> tuple[YAML, dict]:
    """Load projects.yaml preserving comments. Returns (ryaml instance, data)."""
    if not PROJECTS_PATH.exists():
        raise FileNotFoundError(f"Missing required projects file: {PROJECTS_PATH}")
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 120
    with open(PROJECTS_PATH) as f:
        data = ryaml.load(f)
    return ryaml, data


def existing_slugs(data: dict) -> set[str]:
    return {p["name"] for p in data.get("projects", [])}


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
    parser.add_argument("--include-forks", action="store_true", help="Include forked repos")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    parser.add_argument("--min-stars", type=int, default=0, help="Skip repos with fewer than N stars")
    parser.add_argument("--suggest-groups", action="store_true",
                        help="Ask the LLM to suggest group consolidations and print YAML to stdout")
    args = parser.parse_args()

    config = load_config()
    username: str = config["personal"]["github_username"]
    llm_cfg = config.get("llm", {})

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

    skip_list: list[str] = config.get("github_agent", {}).get("skip", [])
    groups: list[dict] = config.get("github_agent", {}).get("groups", [])
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
        print(f"# ---- Suggested groups (paste into {CONFIG_PATH.relative_to(REPO_ROOT)} under github_agent.groups) ----")
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

    ryaml, data = load_projects_yaml()
    known = existing_slugs(data)

    new_repos = [r for r in repos if r["name"] not in known]
    new_groups = [g for g in groups if g["name"] not in known]
    print(f"  {len(known)} already in projects.yaml, {len(new_repos)} new repo(s), {len(new_groups)} new group(s)")

    if not new_repos and not new_groups:
        print("Nothing to add.")
        return

    added = []

    # --- Consolidated group entries ---
    for group in new_groups:
        print(f"\n  [group] {group['name']}")
        entry = make_group_entry(group, username)
        if args.dry_run:
            print(f"    [dry-run] would add consolidated entry: {entry['display_name']}")
            print(f"    repos: {', '.join(group.get('repos', []))}")
        else:
            append_entry(data, entry)
            added.append(group["name"])

    # --- Individual repo entries ---
    for repo in new_repos:
        print(f"\n  + {repo['name']}")
        languages = fetch_repo_languages(repo, token)
        topics = fetch_repo_topics(repo, token)

        if llm_client:
            print("    generating description...")
            description = generate_description(llm_client, llm_cfg.get("model", ""), repo, languages, topics)
        else:
            description = repo.get("description") or ""

        entry = make_entry(repo, languages, topics, description)

        if args.dry_run:
            print(f"    [dry-run] would add: {entry['display_name']}")
            print(f"    description: {description[:80]}...")
        else:
            append_entry(data, entry)
            added.append(repo["name"])

    if not args.dry_run and added:
        with open(PROJECTS_PATH, "w") as f:
            ryaml.dump(data, f)
        print(f"\nWrote {len(added)} new entry(s) to {PROJECTS_PATH.relative_to(REPO_ROOT)}")
        print("Review and fill in: roles, featured, highlights, teaching_context")
    elif args.dry_run:
        total = len(new_repos) + len(new_groups)
        print(f"\n[dry-run] would have added {total} entry(s) ({len(new_groups)} consolidated group(s), {len(new_repos)} individual repo(s))")


if __name__ == "__main__":
    main()
