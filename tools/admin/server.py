"""
Site Admin Agent: FastAPI server
LLM-mediated content editing via GitHub API, protected by an admin key.

Start (dev):  uvicorn server:app --host 127.0.0.1 --port 8600 --reload
Start (prod): uvicorn server:app --host 127.0.0.1 --port 8600
"""

import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

from editor import (
    CONTENT_PROFILE_PATH, CONTENT_PROJECTS_PATH,
    _SHAConflict, apply_edit, get_context, read_file, write_file,
)
from settings_store import (
    SecretDecryptError,
    ensure_projects_file,
    mask_secrets,
    read_profile,
    read_runtime,
    read_secrets,
    write_profile,
    write_runtime,
    write_secrets,
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

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
LLM_API_KEY    = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL   = os.environ.get("LLM_BASE_URL", "http://localhost:8080/v1")
LLM_MODEL      = os.environ.get("LLM_MODEL", "llama-3.1-8b")

# ---------------------------------------------------------------------------
# Admin key auth
# ---------------------------------------------------------------------------

def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    match = re.match(r"^\s*Bearer\s+(.+)\s*$", authorization, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def require_auth(
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="ADMIN_PASSWORD not configured on server")

    provided = (x_admin_key or _extract_bearer_token(authorization)).strip()
    if not provided or not secrets.compare_digest(provided.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key",
        )


# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

# Use <<<CONTEXT>>> as placeholder to avoid .format() conflicts with YAML braces
_SYSTEM_PROMPT_TEMPLATE = """\
You are an admin assistant for a personal portfolio website.
Help the site owner update content via natural language commands.

## What you can edit

<<<PROFILE_PATH>>>, allowed dotted paths:
    personal.name, personal.tagline, personal.domain, personal.email,
    personal.github_username,
  personal.social.github, personal.social.linkedin,
  personal.social.twitter, personal.social.bluesky,
        bio.short, bio.long,
        home_sections, about_sections

<<<PROJECTS_PATH>>>, per-project operations:
  update fields: description_short, description_long, teaching_context (free text)
  set featured: true or false
  set status: active | wip | archived | published
  add a highlight bullet point
  replace the tags list

<<<PROJECTS_PATH>>>, per-collection operations (kind: collection):
    update fields: summary, description_short, description_long, type,
                                 topics (list), platforms (list)
    set featured: true or false
    replace tags list
    replace roles list
    replace members list (member objects with project or repo)

## Hard limits: never do these
- No edits outside data/ (no source code, no .astro files, no workflows, no nginx)
- No deleting projects
- No adding new projects via direct edit operations (use sync_github_projects)
- No changing project names or github URLs
- No changing collection slugs (name)

## Output format
ALWAYS respond with a single valid JSON object. No markdown fences, no preamble.

For a content edit:
{
    "reply": "human-readable description of the change being made",
        "file": "<<<PROFILE_PATH>>>" | "<<<PROJECTS_PATH>>>",
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
    sync_github_projects   -> {"include_forks": false, "min_stars": 0}

For a question about current content (no change needed):
{"reply": "...", "operation": "none"}

For anything out of scope:
{"reply": "I can only help with site content: project and collection content, bio, config fields, tags, roles, and collection members.", "operation": "none"}

## Current site state
<<<CONTEXT>>>
"""


def build_system_prompt(context: str) -> str:
    prompt = _SYSTEM_PROMPT_TEMPLATE.replace("<<<CONTEXT>>>", context)
    prompt = prompt.replace("<<<PROFILE_PATH>>>", CONTENT_PROFILE_PATH)
    prompt = prompt.replace("<<<PROJECTS_PATH>>>", CONTENT_PROJECTS_PATH)
    return prompt


def _operation_file(operation: str) -> str:
    if operation == "update_config_field":
        return CONTENT_PROFILE_PATH
    return CONTENT_PROJECTS_PATH


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


class GitHubSyncRequest(BaseModel):
    include_forks: bool = False
    min_stars: int = 0


class OnboardingBootstrapRequest(BaseModel):
    name: str = Field(min_length=1)
    github_username: str = Field(min_length=1)
    email: str = ""
    domain: str = ""
    tagline: str = ""
    bio_short: str = ""
    github_url: str = ""
    linkedin_url: str = ""
    llm_base_url: str = Field(min_length=1)
    llm_model: str = Field(min_length=1)
    llm_api_key: str = Field(min_length=1)
    github_token: str = Field(min_length=1)


class RuntimeSettingsRequest(BaseModel):
    github_username: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None


class SecretSettingsRequest(BaseModel):
    llm_api_key: str | None = None
    github_token: str | None = None


def _run_github_sync(
    include_forks: bool = False,
    min_stars: int = 0,
    bootstrap: bool = False,
    seed_from_pins: bool = False,
) -> tuple[bool, str]:
    """Run tools/github-agent/agent.py inside its dedicated venv."""
    agent_dir = REPO_ROOT / "tools" / "github-agent"
    venv_dir = agent_dir / ".venv"
    venv_python = venv_dir / "bin" / "python"
    requirements = agent_dir / "requirements.txt"
    agent_script = agent_dir / "agent.py"

    if not venv_python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, cwd=str(agent_dir))

    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-q", "-r", str(requirements)],
        check=True,
        cwd=str(agent_dir),
    )

    runtime = read_runtime()
    try:
        secret_map = read_secrets(ADMIN_PASSWORD) if ADMIN_PASSWORD else {}
    except SecretDecryptError as exc:
        return False, str(exc)

    run_env = os.environ.copy()
    github_username = ((runtime.get("github") or {}).get("username") or "").strip()
    llm_cfg = runtime.get("llm") or {}
    if github_username:
        run_env["GITHUB_USERNAME"] = github_username
    if llm_cfg.get("base_url"):
        run_env["LLM_BASE_URL"] = str(llm_cfg["base_url"])
    if llm_cfg.get("model"):
        run_env["LLM_MODEL"] = str(llm_cfg["model"])
    if secret_map.get("llm_api_key"):
        run_env["LLM_API_KEY"] = secret_map["llm_api_key"]
    if secret_map.get("github_token"):
        run_env["GITHUB_TOKEN"] = secret_map["github_token"]

    cmd = [str(venv_python), str(agent_script)]
    if bootstrap:
        cmd.append("--bootstrap")
    if seed_from_pins:
        cmd.append("--seed-from-pins")
    if include_forks:
        cmd.append("--include-forks")
    if min_stars > 0:
        cmd.extend(["--min-stars", str(min_stars)])

    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    merged = "\n".join(part for part in [output, err] if part).strip()
    summarized = _summarize_sync_output(merged)
    if proc.returncode != 0:
        return False, summarized or "GitHub sync failed"
    return True, summarized or "GitHub sync complete"


def _summarize_sync_output(text: str) -> str:
    if not text:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    llm_warn_count = sum(1 for line in lines if line.startswith("[warn] LLM call failed for"))
    rate_limit_count = sum(1 for line in lines if "rate limit" in line.lower() or "429" in line)

    keep: list[str] = []
    keep_patterns = (
        "Fetching public repos",
        "Found ",
        "Wrote raw snapshot",
        "Seeding curated projects",
        "already curated",
        "Wrote ",
        "No curated additions needed",
        "[warn] GitHub token rejected",
        "[warn] Could not fetch pinned repos",
        "[error]",
    )

    for line in lines:
        if line.startswith(keep_patterns):
            keep.append(line)

    if llm_warn_count:
        keep.append(f"[warn] LLM description generation failed for {llm_warn_count} repos (fallback descriptions used)")
    if rate_limit_count:
        keep.append(f"[warn] LLM rate limit indicators seen {rate_limit_count} times")

    if not keep:
        keep = lines[-8:]

    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for line in keep:
        if line not in seen:
            seen.add(line)
            deduped.append(line)
    return "\n".join(deduped[:20])



@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text())


@app.post("/chat")
async def chat(req: ChatRequest, _auth: None = Depends(require_auth)) -> JSONResponse:
    global conversation

    runtime = read_runtime()
    llm_cfg = runtime.get("llm") or {}
    try:
        secret_map = read_secrets(ADMIN_PASSWORD) if ADMIN_PASSWORD else {}
    except SecretDecryptError as exc:
        return JSONResponse({"reply": f"Secrets error: {exc}", "operation": "none", "committed": False})
    llm_api_key = secret_map.get("llm_api_key") or LLM_API_KEY
    llm_base_url = str(llm_cfg.get("base_url") or LLM_BASE_URL)
    llm_model = str(llm_cfg.get("model") or LLM_MODEL)

    # Validate required config
    missing = [k for k, v in {
        "LLM_API_KEY": llm_api_key,
    }.items() if not v]
    if missing:
        return JSONResponse({
            "reply": f"Server misconfigured; set these env vars: {', '.join(missing)}",
            "operation": "none",
            "committed": False,
        })

    llm = OpenAI(api_key=llm_api_key, base_url=llm_base_url)

    # Fetch live local context for every turn (keeps it fresh)
    try:
        context = get_context()
    except Exception as exc:
        context = f"(could not fetch site state: {exc})"

    system = build_system_prompt(context)
    conversation.append({"role": "user", "content": req.message})
    messages = [{"role": "system", "content": system}] + conversation[-12:]  # last 6 turns

    try:
        resp = llm.chat.completions.create(
            model=llm_model,
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

    if operation == "sync_github_projects":
        args = intent.get("args", {})
        include_forks = bool(args.get("include_forks", False))
        min_stars = int(args.get("min_stars", 0) or 0)
        ok, sync_output = _run_github_sync(include_forks=include_forks, min_stars=min_stars)
        committed = ok
        if ok:
            runtime = read_runtime()
            runtime["setup"]["last_sync_at"] = datetime.now(timezone.utc).isoformat()
            write_runtime(runtime)
            reply = reply or "GitHub sync complete."
            if sync_output:
                reply += f"\n\n{sync_output}"
        else:
            reply = f"GitHub sync failed.\n\n{sync_output}"
    elif operation and operation != "none":
        try:
            file_path = _operation_file(operation)
            for attempt in range(3):
                content, sha = read_file(file_path)
                updated, summary = apply_edit(content, intent)
                try:
                    write_file(file_path, updated, sha, f"[admin] {summary}")
                    break
                except _SHAConflict as e:
                    if attempt == 2:
                        raise RuntimeError(f"SHA conflict after 3 attempts: {e}") from e
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


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/onboarding/state", dependencies=[Depends(require_auth)])
async def onboarding_state() -> JSONResponse:
    runtime = read_runtime()
    profile_exists = (REPO_ROOT / CONTENT_PROFILE_PATH).exists()
    projects_exists = (REPO_ROOT / CONTENT_PROJECTS_PATH).exists()
    initialized = bool(((runtime.get("setup") or {}).get("initialized")))
    try:
        secret_view = mask_secrets(read_secrets(ADMIN_PASSWORD)) if ADMIN_PASSWORD else {}
    except SecretDecryptError as exc:
        secret_view = {"error": str(exc)}
    return JSONResponse(
        {
            "initialized": initialized and profile_exists and projects_exists,
            "profile_exists": profile_exists,
            "projects_exists": projects_exists,
            "runtime": runtime,
            "secrets": secret_view,
        }
    )


@app.post("/onboarding/bootstrap", dependencies=[Depends(require_auth)])
async def onboarding_bootstrap(req: OnboardingBootstrapRequest) -> JSONResponse:
    name = req.name.strip()
    github_username = req.github_username.strip()
    llm_base_url = req.llm_base_url.strip()
    llm_model = req.llm_model.strip()
    llm_api_key = req.llm_api_key.strip()
    github_token = req.github_token.strip()

    missing: list[str] = []
    if not name:
        missing.append("name")
    if not github_username:
        missing.append("github_username")
    if not llm_base_url:
        missing.append("llm_base_url")
    if not llm_model:
        missing.append("llm_model")
    if not llm_api_key:
        missing.append("llm_api_key")
    if not github_token:
        missing.append("github_token")
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required onboarding fields: {', '.join(missing)}")

    profile = read_profile()
    profile["personal"]["name"] = name
    profile["personal"]["tagline"] = req.tagline.strip()
    profile["personal"]["domain"] = req.domain.strip()
    profile["personal"]["email"] = req.email.strip()
    profile["personal"]["github_username"] = github_username
    profile["personal"]["social"]["github"] = req.github_url.strip() or f"https://github.com/{github_username}"
    profile["personal"]["social"]["linkedin"] = req.linkedin_url.strip()
    profile["bio"]["short"] = req.bio_short.strip()
    write_profile(profile)

    runtime = read_runtime()
    runtime["github"]["username"] = github_username
    runtime["llm"]["base_url"] = llm_base_url
    runtime["llm"]["model"] = llm_model
    runtime["setup"]["initialized"] = False
    write_runtime(runtime)

    if ADMIN_PASSWORD:
        write_secrets(
            ADMIN_PASSWORD,
            {
                "llm_api_key": llm_api_key,
                "github_token": github_token,
            },
        )

    ensure_projects_file()
    ok, output = _run_github_sync(bootstrap=True, seed_from_pins=True)
    if ok:
        runtime = read_runtime()
        runtime["setup"]["initialized"] = True
        runtime["setup"]["last_bootstrap_at"] = datetime.now(timezone.utc).isoformat()
        write_runtime(runtime)
    status_code = 200 if ok else 500
    return JSONResponse({"ok": ok, "message": output}, status_code=status_code)


@app.get("/settings/runtime", dependencies=[Depends(require_auth)])
async def get_runtime_settings() -> JSONResponse:
    return JSONResponse(read_runtime())


@app.post("/settings/runtime", dependencies=[Depends(require_auth)])
async def update_runtime_settings(req: RuntimeSettingsRequest) -> JSONResponse:
    runtime = read_runtime()
    if req.github_username is not None:
        runtime["github"]["username"] = req.github_username.strip()
    if req.llm_base_url is not None:
        runtime["llm"]["base_url"] = req.llm_base_url.strip()
    if req.llm_model is not None:
        runtime["llm"]["model"] = req.llm_model.strip()
    write_runtime(runtime)
    return JSONResponse({"ok": True, "runtime": runtime})


@app.get("/settings/secrets", dependencies=[Depends(require_auth)])
async def get_secret_settings() -> JSONResponse:
    if not ADMIN_PASSWORD:
        return JSONResponse({"ok": False, "message": "ADMIN_PASSWORD is not configured"}, status_code=500)
    try:
        current = read_secrets(ADMIN_PASSWORD)
    except SecretDecryptError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "secrets": mask_secrets(current)})


@app.post("/settings/secrets", dependencies=[Depends(require_auth)])
async def update_secret_settings(req: SecretSettingsRequest) -> JSONResponse:
    if not ADMIN_PASSWORD:
        return JSONResponse({"ok": False, "message": "ADMIN_PASSWORD is not configured"}, status_code=500)
    try:
        updated = write_secrets(
            ADMIN_PASSWORD,
            {
                "llm_api_key": req.llm_api_key,
                "github_token": req.github_token,
            },
        )
    except SecretDecryptError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "secrets": mask_secrets(updated)})


@app.post("/github/sync", dependencies=[Depends(require_auth)])
async def github_sync(req: GitHubSyncRequest) -> JSONResponse:
    ok, output = _run_github_sync(include_forks=req.include_forks, min_stars=req.min_stars, bootstrap=False)
    if not ok:
        return JSONResponse({"ok": False, "message": output}, status_code=500)
    runtime = read_runtime()
    runtime["setup"]["last_sync_at"] = datetime.now(timezone.utc).isoformat()
    write_runtime(runtime)
    return JSONResponse({"ok": True, "message": output})


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
