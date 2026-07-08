# DotProfile

A self-hosted personal profile platform for builders. Run it locally, edit your own content, and deploy directly to your VPS.

![Astro](https://img.shields.io/badge/Astro-5-BC52EE?logo=astro&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-009639?logo=nginx&logoColor=white)

The site is a static Astro build deployed over rsync to a VPS. Platform/demo data lives in `data/*.yaml`; user-managed content and runtime state live in `data/user/`.

**Included:**
- Home page with hero and featured projects
- Projects hub with per-project detail pages (driven by `data/user/projects.yaml`)
- About page with configurable collection sections
- Contact page
- Local admin agent for making edits to runtime YAML content

**Planned:**
- **GitHub agent**: syncs new content or repos into `data/user/projects.yaml` via GitHub API
- **Job search**: job board monitoring, resume tailoring & application tracking
- **Social post generator**: turns PRs, tags or releases into LinkedIn/Bluesky drafts
- **Project blog**: turns git history into "build-in-public" style blog posts

## Setup

### 1. Clone

```bash
git clone https://github.com/gperdrizet/dotprofile.git
cd dotprofile
```

### 2. Configure environment

Copy the template and set your admin key.

```bash
cp .env.template .env
```

Required on first boot:

```bash
ADMIN_PASSWORD=your-strong-admin-key
```

Optional in `.env` (can be set later in onboarding/settings UI):
- `GITHUB_USERNAME`
- `GITHUB_TOKEN`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `LLM_API_KEY`

### 3. Install dependencies

Requires Node 22+. Install with [fnm](https://github.com/Schniz/fnm) or nvm.

```bash
cd site
npm install
```

### 4. Start admin and complete onboarding

Run admin first:

```bash
make admin
# -> http://127.0.0.1:8600
```

On first run, complete the onboarding form in the admin UI. Onboarding writes these files under `data/user/`:
- `profile.yaml` (public profile content)
- `projects.yaml` (curated projects and collections)
- `projects.raw.yaml` (raw sync library)
- `runtime.json` (non-secret runtime settings)
- `secrets.enc.json` (encrypted secrets)

`config.yaml` is legacy and is no longer the primary runtime configuration file.

### 5. Run site locally

```bash
cd site
npm run dev
# → http://localhost:4321
```

The site redirects first-time visitors to `/setup` until onboarding is complete.

### 6. Edit content after onboarding

Use the admin UI to update profile text, project descriptions, tags, collection sections, and runtime/secrets settings.

If you prefer manual edits, use these content files:
- `data/user/profile.yaml`
- `data/user/projects.yaml`

Example `data/user/projects.yaml` entry:

```yaml
projects:
  - name: my-project
    display_name: My Project
    status: published
    featured: true
    tags: [python, llm]
    roles: [llm-engineer]
    github: https://github.com/you/my-project
    service_url: https://my-project.yourdomain.com
    description_short: >
      One or two sentence summary shown on cards.
    highlights:
      - Resume bullet point one
      - Resume bullet point two
```

Collection sections are configured in `data/user/profile.yaml`:

```yaml
home_sections:
  - collection: speaking
    title: Speaking

about_sections:
  - collection: open-source
    title: Open Source
```

### 7. Run admin locally (optional)

```bash
make admin
# → http://127.0.0.1:8600
```

The admin UI edits local files in `data/user/` directly.

## Deployment

Deploy directly from your machine to a public VPS using rsync. This is the only supported deployment path in this repository.

### Prerequisites on the server

```bash
# Create the deploy directories (needs sudo once)
sudo mkdir -p /opt/yoursite /opt/yoursite-staging
sudo chown youruser:youruser /opt/yoursite /opt/yoursite-staging
```

Add nginx configs to serve both directories; see `configs/nginx/` for reference configs.

### Local environment

Set deploy variables in `.env`:

```bash
GATEKEEPER_HOST=your-server-ip
GATEKEEPER_USER=your-ssh-user
```

Your SSH key must already be trusted by the server via `~/.ssh/authorized_keys`.

### Deploy commands

```bash
# Staging
make deploy-staging

# Production (interactive confirmation)
make deploy-prod
```

`make deploy-prod` prompts for confirmation before uploading.

### LLM tools (optional)

Set `LLM_API_KEY` in your environment for the resume and social tools if your model endpoint requires auth:

```bash
export LLM_API_KEY=your-key-here
```

Any OpenAI-compatible endpoint works: llama.cpp, Ollama, OpenAI, etc. Configure URL/model in admin settings (stored in `data/user/runtime.json`), and keep API keys in encrypted secrets (`data/user/secrets.enc.json`).

## Scope

DotProfile intentionally does not document or support GitHub Actions-based deployment.

- Supported: local development, local testing, direct VPS deploy.
- Optional for users: adapt this to any CI/CD path they prefer.
