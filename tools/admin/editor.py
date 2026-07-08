"""
Constrained edit operations for the site admin agent.

Only content YAML files are editable.
Every operation is validated in Python; the LLM never bypasses these checks.
"""

import hashlib
import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

CONTENT_ROOT = os.environ.get("CONTENT_ROOT", "data/user").strip("/")
CONTENT_CONFIG_PATH = f"{CONTENT_ROOT}/config.yaml"
CONTENT_PROJECTS_PATH = f"{CONTENT_ROOT}/projects.yaml"

# Hard allow-list: content files only
ALLOWED_PATHS = frozenset({
    CONTENT_CONFIG_PATH,
    CONTENT_PROJECTS_PATH,
})

# Dotted config.yaml paths the LLM may update
ALLOWED_CONFIG_PATHS = frozenset({
    "personal.tagline",
    "personal.email",
    "personal.social.github",
    "personal.social.linkedin",
    "personal.social.twitter",
    "personal.social.bluesky",
    "bio.short",
    "bio.long",
    "teaching.active",
    "teaching.summary",
    "home_sections",
})

# Free-text project fields the LLM may overwrite
ALLOWED_PROJECT_FIELDS = frozenset({
    "description_short",
    "description_long",
    "teaching_context",
})

ALLOWED_COLLECTION_FIELDS = frozenset({
    "summary",
    "description_short",
    "description_long",
    "type",
    "topics",
    "platforms",
})

ALLOWED_STATUSES = frozenset({"active", "wip", "archived", "published"})


# ---------------------------------------------------------------------------
# Local file helpers
# ---------------------------------------------------------------------------

def _resolve_path(path: str) -> Path:
    if path not in ALLOWED_PATHS:
        raise ValueError(f"Path not allowed: {path!r}")
    return REPO_ROOT / path


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def read_file(path: str, _token: str = "", _repo: str = "", _branch: str = "main") -> tuple[str, str]:
    """Fetch a local file. Returns (content, hash)."""
    file_path = _resolve_path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Missing content file: {path}")
    content = file_path.read_text(encoding="utf-8")
    return content, _hash_content(content)


def write_file(path: str, content: str, sha: str, _message: str = "", _token: str = "", _repo: str = "",
               _branch: str = "main") -> None:
    """Write updated content to a local file with optimistic hash check."""
    file_path = _resolve_path(path)
    current = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    current_hash = _hash_content(current)
    if current and current_hash != sha:
        raise _SHAConflict("stale content hash")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


class _SHAConflict(Exception):
    """Raised when local file content has changed between read and write."""


# ---------------------------------------------------------------------------
def get_context(token: str = "", repo: str = "", branch: str = "main") -> str:
    """Return a concise summary of current site content for the LLM system prompt."""
    lines: list[str] = []
    config_path, projects_path = CONTENT_CONFIG_PATH, CONTENT_PROJECTS_PATH

    try:
        raw, _ = read_file(config_path, token, repo, branch=branch)
        cfg = yaml.safe_load(raw)
        p = cfg.get("personal", {})
        b = cfg.get("bio", {})
        t = cfg.get("teaching", {})
        soc = p.get("social", {})
        lines += [
            f"### {config_path}",
            f"personal.name:           {p.get('name', '')}",
            f"personal.tagline:        {p.get('tagline', '')}",
            f"personal.email:          {p.get('email', '')}",
            f"personal.social.github:  {soc.get('github', '')}",
            f"personal.social.linkedin:{soc.get('linkedin', '')}",
            f"bio.short:               {str(b.get('short', ''))[:200]}",
            f"teaching.active:         {t.get('active', False)}",
            f"teaching.summary:        {str(t.get('summary', ''))[:200]}",
        ]
    except Exception as exc:
        lines.append(f"({config_path} unavailable: {exc})")

    try:
        raw, _ = read_file(projects_path, token, repo, branch=branch)
        projects = yaml.safe_load(raw).get("projects", [])
        lines.append(f"\n### {projects_path}  (kind | name | status/type | featured | tags)")
        for proj in projects:
            kind = proj.get("kind", "project")
            status_or_type = proj.get("status", "") if kind == "project" else proj.get("type", "")
            lines.append(
                f"  {kind:<10} | {proj['name']:<28} | {status_or_type:<10} | "
                f"featured:{proj.get('featured', False)} | {proj.get('tags', [])}"
            )
    except Exception as exc:
        lines.append(f"({projects_path} unavailable: {exc})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Edit application
# ---------------------------------------------------------------------------

def apply_edit(file_content: str, intent: dict) -> tuple[str, str]:
    """
    Apply a validated edit intent to YAML content.
    Returns (updated_yaml_string, human_readable_summary).
    Raises ValueError for any disallowed or malformed operation.
    """
    op = intent.get("operation", "")
    args = intent.get("args", {})
    data = yaml.safe_load(file_content)

    if op == "update_config_field":
        path: str = args.get("path", "")
        if path not in ALLOWED_CONFIG_PATHS:
            raise ValueError(f"Config path not allowed: {path!r}")
        _set_nested(data, path.split("."), args["value"])
        summary = f"config: {path} = {str(args['value'])[:80]!r}"

    elif op == "update_project_field":
        field = args.get("field", "")
        if field not in ALLOWED_PROJECT_FIELDS:
            raise ValueError(f"Project field not allowed: {field!r}")
        proj = _find_project(data, args["project"])
        proj[field] = args["value"]
        summary = f"{args['project']}.{field} updated"

    elif op == "set_project_featured":
        proj = _find_project(data, args["project"])
        proj["featured"] = bool(args["value"])
        summary = f"{args['project']}.featured = {bool(args['value'])}"

    elif op == "set_project_status":
        status_val = args.get("status", "")
        if status_val not in ALLOWED_STATUSES:
            raise ValueError(f"Invalid status {status_val!r}. Allowed: {sorted(ALLOWED_STATUSES)}")
        proj = _find_project(data, args["project"])
        proj["status"] = status_val
        summary = f"{args['project']}.status = {status_val}"

    elif op == "add_project_highlight":
        proj = _find_project(data, args["project"])
        if not isinstance(proj.get("highlights"), list):
            proj["highlights"] = []
        proj["highlights"].append(args["highlight"])
        summary = f"added highlight to {args['project']}"

    elif op == "update_project_tags":
        tags = args.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError("tags must be a list of strings")
        proj = _find_project(data, args["project"])
        proj["tags"] = tags
        summary = f"{args['project']}.tags = {tags}"

    elif op == "update_collection_field":
        field = args.get("field", "")
        if field not in ALLOWED_COLLECTION_FIELDS:
            raise ValueError(f"Collection field not allowed: {field!r}")
        coll = _find_collection(data, args["collection"])
        if field in {"topics", "platforms"}:
            value = args.get("value", [])
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{field} must be a list of strings")
            coll[field] = value
        else:
            coll[field] = args["value"]
        summary = f"{args['collection']}.{field} updated"

    elif op == "set_collection_featured":
        coll = _find_collection(data, args["collection"])
        coll["featured"] = bool(args["value"])
        summary = f"{args['collection']}.featured = {bool(args['value'])}"

    elif op == "update_collection_tags":
        tags = args.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise ValueError("tags must be a list of strings")
        coll = _find_collection(data, args["collection"])
        coll["tags"] = tags
        summary = f"{args['collection']}.tags = {tags}"

    elif op == "update_collection_roles":
        roles = args.get("roles", [])
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise ValueError("roles must be a list of strings")
        coll = _find_collection(data, args["collection"])
        coll["roles"] = roles
        summary = f"{args['collection']}.roles = {roles}"

    elif op == "update_collection_members":
        members = args.get("members", [])
        if not isinstance(members, list):
            raise ValueError("members must be a list")
        normalized: list[dict[str, str]] = []
        for member in members:
            if not isinstance(member, dict):
                raise ValueError("each member must be an object")
            if "project" in member:
                if not isinstance(member["project"], str):
                    raise ValueError("member.project must be a string")
                normalized.append({"project": member["project"]})
            elif "repo" in member:
                if not isinstance(member["repo"], str):
                    raise ValueError("member.repo must be a string")
                repo_member = {"repo": member["repo"]}
                if "label" in member:
                    if not isinstance(member["label"], str):
                        raise ValueError("member.label must be a string")
                    repo_member["label"] = member["label"]
                if "url" in member:
                    if not isinstance(member["url"], str):
                        raise ValueError("member.url must be a string")
                    repo_member["url"] = member["url"]
                normalized.append(repo_member)
            else:
                raise ValueError("member must include either project or repo")
        coll = _find_collection(data, args["collection"])
        coll["members"] = normalized
        summary = f"{args['collection']}.members updated"

    else:
        raise ValueError(f"Unknown operation: {op!r}")

    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False), summary


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _set_nested(d: dict, keys: list[str], value: Any) -> None:
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _find_project(data: dict, name: str) -> dict:
    for p in data.get("projects", []):
        if p.get("name") == name and p.get("kind", "project") != "collection":
            return p
    raise ValueError(f"Project not found: {name!r}")


def _find_collection(data: dict, name: str) -> dict:
    for p in data.get("projects", []):
        if p.get("name") == name and p.get("kind") == "collection":
            return p
    raise ValueError(f"Collection not found: {name!r}")
