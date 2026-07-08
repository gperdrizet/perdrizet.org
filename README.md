# DotProfile

A self-hosted personal profile platform for builders. Run it locally, edit your own content, and deploy directly to your VPS.

[![Test](https://github.com/gperdrizet/dotprofile/actions/workflows/test.yml/badge.svg)](https://github.com/gperdrizet/dotprofile/actions/workflows/test.yml)

![Astro](https://img.shields.io/badge/Astro-5-BC52EE?logo=astro&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-009639?logo=nginx&logoColor=white)

The site is a static Astro build deployed over rsync to a VPS. Platform/demo data lives in `data/*.yaml`; runtime content lives in `data/user/*.yaml`.

**Included:**
- Home page with hero and featured projects
- Projects hub with per-project detail pages (driven by `data/user/projects.yaml`)
- About page with configurable collection sections
- Contact page
- Local admin agent for making edits to runtime YAML content
- Direct deployment commands for staging and production

**Planned:**
- GitHub agent: syncs new repos into `data/user/projects.yaml` via GitHub API + LLM descriptions
- Resume tailoring: gap-analysis against a job posting, rewrites bullets, exports PDF
- Social post generator: turns project highlights into LinkedIn/Bluesky drafts

## Setup

### 1. Clone

```bash
git clone https://github.com/gperdrizet/dotprofile.git
cd dotprofile
```

### 2. Edit `data/user/config.yaml`

This is the only file you need to change for a basic deployment. Everything flows from here.

```yaml
personal:
  name: Your Name
  tagline: "Your · Role · Tags"
  domain: yourdomain.com
  email: you@yourdomain.com
  github_username: your-github-username
  social:
    linkedin: https://www.linkedin.com/in/yourprofile/
    github: https://github.com/your-github-username

bio:
  short: >
    One or two sentences that appear on the home page and About page.
  long: ""   # Optional; leave blank to reuse `short`

home_sections: []
about_sections: []

llm:
  base_url: http://localhost:8080/v1   # Any OpenAI-compatible endpoint
  model: your-model-name

deploy:
  staging_path: /opt/yoursite-staging
  prod_path: /opt/yoursite
```

### 3. Edit `data/user/projects.yaml`

Add your projects. Each entry becomes a card on the Projects page and a dedicated `/projects/<name>` page.

```yaml
projects:
  - name: my-project            # slug, must be URL-safe
    display_name: My Project
    status: live                # live | published | wip | archived
    featured: true              # pins to home page
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

Collection sections on Home/About are fully configurable in `data/user/config.yaml` by listing collection slugs:

```yaml
home_sections:
  - collection: speaking
    title: Speaking

about_sections:
  - collection: open-source
    title: Open Source
```

### 4. Install dependencies

Requires Node 22+. Install with [fnm](https://github.com/Schniz/fnm) or nvm.

```bash
cd site
npm install
```

### 5. Run locally

```bash
cd site
npm run dev
# → http://localhost:4321
```

### 6. Run admin locally (optional)

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

Set `LLM_API_KEY` in your environment for the resume and social tools:

```bash
export LLM_API_KEY=your-key-here
```

Any OpenAI-compatible endpoint works: llama.cpp, Ollama, OpenAI, etc. Configure the URL and model in `data/user/config.yaml` under `llm:`.

## Scope

DotProfile intentionally does not document or support GitHub Actions-based deployment.

- Supported: local development, local testing, direct VPS deploy.
- Optional for users: adapt this to any CI/CD path they prefer.
