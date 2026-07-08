"""
Site Admin Agent: FastAPI server
LLM-mediated content editing via GitHub API, protected by HTTP Basic auth.

Start (dev):  uvicorn server:app --host 127.0.0.1 --port 8600 --reload
Start (prod): uvicorn server:app --host 127.0.0.1 --port 8600
"""

import json
import os
import re
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

from editor import (
    _SHAConflict, apply_edit, create_pr, get_context, get_open_pr,
    merge_pr, read_file, trigger_workflow, write_file,
)

# ---------------------------------------------------------------------------
# .env loader (same pattern as tools/github-agent)
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
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
# Config from environment
# ---------------------------------------------------------------------------

ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO    = os.environ.get("GITHUB_REPO", "")   # owner/repo  e.g. gperdrizet/perdrizet.org
GITHUB_BRANCH  = os.environ.get("GITHUB_BRANCH", "dev")
LLM_API_KEY    = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL   = os.environ.get("LLM_BASE_URL", "http://localhost:8080/v1")
LLM_MODEL      = os.environ.get("LLM_MODEL", "llama-3.1-8b")

# ---------------------------------------------------------------------------
# HTTP Basic auth
# ---------------------------------------------------------------------------

security = HTTPBasic()


def require_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD not configured on server")
    user_ok = secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), ADMIN_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="Site Admin"'},
        )
    return credentials.username


# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

# Use <<<CONTEXT>>> as placeholder to avoid .format() conflicts with YAML braces
_SYSTEM_PROMPT_TEMPLATE = """\
You are an admin assistant for a personal portfolio website.
Help the site owner update content via natural language commands.

## What you can edit

data/config.yaml, allowed dotted paths:
  personal.tagline, personal.email,
  personal.social.github, personal.social.linkedin,
  personal.social.twitter, personal.social.bluesky,
    bio.short, bio.long, teaching.active, teaching.summary,
    home_sections

data/projects.yaml, per-project operations:
  update fields: description_short, description_long, teaching_context (free text)
  set featured: true or false
  set status: active | wip | archived | published
  add a highlight bullet point
  replace the tags list

data/projects.yaml, per-collection operations (kind: collection):
    update fields: summary, description_short, description_long, type,
                                 topics (list), platforms (list)
    set featured: true or false
    replace tags list
    replace roles list
    replace members list (member objects with project or repo)

## Hard limits: never do these
- No edits outside data/ (no source code, no .astro files, no workflows, no nginx)
- No deleting projects
- No adding new projects (make sync-projects handles that)
- No changing project names or github URLs
- No changing collection slugs (name)

## Output format
ALWAYS respond with a single valid JSON object. No markdown fences, no preamble.

For a content edit:
{
  "reply": "human-readable description of the change being made",
  "file": "data/config.yaml" | "data/projects.yaml",
  "operation": "<operation name>",
  "args": { ... }
}

Operations and their args:
  update_config_field   -> {"path": "personal.tagline", "value": "new value"}
  update_project_field  -> {"project": "project-slug", "field": "description_short", "value": "..."}
  set_project_featured  -> {"project": "project-slug", "value": true}
  set_project_status    -> {"project": "project-slug", "status": "active"}
  add_project_highlight -> {"project": "project-slug", "highlight": "Reduced latency by 40%"}
  update_project_tags   -> {"project": "project-slug", "tags": ["python", "docker"]}
    update_collection_field   -> {"collection": "collection-slug", "field": "summary", "value": "..."}
    set_collection_featured   -> {"collection": "collection-slug", "value": true}
    update_collection_tags    -> {"collection": "collection-slug", "tags": ["teaching", "python"]}
    update_collection_roles   -> {"collection": "collection-slug", "roles": ["educator"]}
    update_collection_members -> {"collection": "collection-slug", "members": [{"project": "bug-hunter"}, {"repo": "owner/repo", "label": "Repo label"}]}

For a question about current content (no change needed):
{"reply": "...", "operation": "none"}

For anything out of scope:
{"reply": "I can only help with site content: project and collection content, bio, config fields, tags, roles, and collection members.", "operation": "none"}

## Current site state
<<<CONTEXT>>>
"""


def build_system_prompt(context: str) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.replace("<<<CONTEXT>>>", context)


# ---------------------------------------------------------------------------
# Conversation history (in-memory; fine for single admin user)
# ---------------------------------------------------------------------------

conversation: list[dict] = []

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(title="Site Admin", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    message: str


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def root() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text())


@app.post("/chat")
async def chat(req: ChatRequest, _user: str = Depends(require_auth)) -> JSONResponse:
    global conversation

    # Validate required config
    missing = [k for k, v in {
        "GITHUB_TOKEN": GITHUB_TOKEN,
        "GITHUB_REPO":  GITHUB_REPO,
        "LLM_API_KEY":  LLM_API_KEY,
    }.items() if not v]
    if missing:
        return JSONResponse({
            "reply": f"Server misconfigured; set these env vars: {', '.join(missing)}",
            "operation": "none",
            "committed": False,
        })

    llm = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    # Fetch live context from GitHub for every turn (keeps it fresh)
    try:
        context = get_context(GITHUB_TOKEN, GITHUB_REPO)
    except Exception as exc:
        context = f"(could not fetch site state: {exc})"

    system = build_system_prompt(context)
    conversation.append({"role": "user", "content": req.message})
    messages = [{"role": "system", "content": system}] + conversation[-12:]  # last 6 turns

    try:
        resp = llm.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            max_tokens=600,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as exc:
        conversation.pop()
        return JSONResponse({"reply": f"LLM error: {exc}", "operation": "none", "committed": False})

    intent = _parse_json(raw)
    reply = intent.get("reply", "Done.")
    operation = intent.get("operation", "none")
    committed = False

    if operation and operation != "none":
        try:
            file_path = intent["file"]
            for attempt in range(3):
                content, sha = read_file(file_path, GITHUB_TOKEN, GITHUB_REPO, branch=GITHUB_BRANCH)
                updated, summary = apply_edit(content, intent)
                try:
                    write_file(file_path, updated, sha, f"[admin] {summary}", GITHUB_TOKEN,
                               GITHUB_REPO, branch=GITHUB_BRANCH)
                    break
                except _SHAConflict as e:
                    if attempt == 2:
                        raise RuntimeError(f"SHA conflict after 3 attempts: {e}")
            committed = True
        except Exception as exc:
            reply += f"\n\n⚠️ Edit failed: {exc}"

    conversation.append({"role": "assistant", "content": reply})
    return JSONResponse({"reply": reply, "operation": operation, "committed": committed})


@app.delete("/chat/history", dependencies=[Depends(require_auth)])
async def clear_history() -> dict:
    global conversation
    conversation = []
    return {"cleared": True}


# ---------------------------------------------------------------------------
# PR and deployment endpoints
# ---------------------------------------------------------------------------

@app.get("/pr/status")
async def pr_status(_user: str = Depends(require_auth)) -> JSONResponse:
    """Return the current open PR from GITHUB_BRANCH → main, if any."""
    try:
        pr = get_open_pr(GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH)
        if pr:
            return JSONResponse({"pr": {"number": pr["number"], "title": pr["title"],
                                        "url": pr["html_url"], "state": pr["state"]}})
        return JSONResponse({"pr": None})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/pr/create")
async def pr_create(_user: str = Depends(require_auth)) -> JSONResponse:
    """Open a PR from GITHUB_BRANCH → main."""
    try:
        existing = get_open_pr(GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH)
        if existing:
            return JSONResponse({"pr": {"number": existing["number"], "title": existing["title"],
                                        "url": existing["html_url"]}, "already_exists": True})
        pr = create_pr(GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH)
        return JSONResponse({"pr": {"number": pr["number"], "title": pr["title"],
                                    "url": pr["html_url"]}, "already_exists": False})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/pr/merge")
async def pr_merge(_user: str = Depends(require_auth)) -> JSONResponse:
    """Merge the open PR from GITHUB_BRANCH → main."""
    try:
        pr = get_open_pr(GITHUB_TOKEN, GITHUB_REPO, GITHUB_BRANCH)
        if not pr:
            return JSONResponse({"error": "No open PR found."}, status_code=404)
        result = merge_pr(GITHUB_TOKEN, GITHUB_REPO, pr["number"])
        return JSONResponse({"merged": result.get("merged", False), "message": result.get("message", "")})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


class DeployRequest(BaseModel):
    version: str


@app.post("/deploy")
async def deploy_production(req: DeployRequest, _user: str = Depends(require_auth)) -> JSONResponse:
    """Trigger the production deploy workflow."""
    version = req.version.strip()
    if not version:
        return JSONResponse({"error": "version is required"}, status_code=400)
    try:
        trigger_workflow(GITHUB_TOKEN, GITHUB_REPO, "deploy-prod.yml",
                         inputs={"version": version, "confirm": "deploy"})
        return JSONResponse({"triggered": True, "version": version})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict:
    """Parse LLM response as JSON, with fallback extraction if wrapped in prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"reply": text, "operation": "none"}
