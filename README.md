# Personal Brand Platform

A generalizable, self-hosted personal brand platform for AI/ML practitioners. Fork it, point it at your data, and deploy.

[![Test](https://github.com/gperdrizet/perdrizet.org/actions/workflows/test.yml/badge.svg)](https://github.com/gperdrizet/perdrizet.org/actions/workflows/test.yml)
[![Deploy Staging](https://github.com/gperdrizet/perdrizet.org/actions/workflows/deploy-staging.yml/badge.svg)](https://github.com/gperdrizet/perdrizet.org/actions/workflows/deploy-staging.yml)
[![Deploy Production](https://github.com/gperdrizet/perdrizet.org/actions/workflows/deploy-prod.yml/badge.svg)](https://github.com/gperdrizet/perdrizet.org/actions/workflows/deploy-prod.yml)

![Astro](https://img.shields.io/badge/Astro-5-BC52EE?logo=astro&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?logo=githubactions&logoColor=white)
![nginx](https://img.shields.io/badge/nginx-009639?logo=nginx&logoColor=white)

The site is a static Astro build deployed over rsync to a VPS. All personal data (name, bio, social links, project list, resume) lives in two YAML files. The rest of the codebase is generic.

**Included:**
- Home page with hero and featured projects
- Projects hub with per-project detail pages (driven by `data/projects.yaml`)
- About page with bio and interests
- Contact page
- Agent for making edits and updates to live page contents
- CI/CD: auto-deploy to staging on push, manual deploy to production with version tag

**Planned:**
- GitHub agent: syncs new repos into `data/projects.yaml` via GitHub API + LLM descriptions
- Resume tailoring: gap-analysis against a job posting, rewrites bullets, exports PDF
- Social post generator: turns project highlights into LinkedIn/Bluesky drafts

## Setup

### 1. Fork and clone

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_FORK.git
cd YOUR_FORK
```

### 2. Edit `data/config.yaml`

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

teaching:
  active: false   # Set true to show teaching section on Home + About
  summary: ""
  platforms: []
  topics: []

llm:
  base_url: http://localhost:8080/v1   # Any OpenAI-compatible endpoint
  model: your-model-name

deploy:
  staging_path: /opt/yoursite-staging
  prod_path: /opt/yoursite
```

### 3. Edit `data/projects.yaml`

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

## Deployment

The CI/CD pipeline is designed for deployment to a public VPS: rsync static files to a pre-configured nginx directory on the server.

### Prerequisites on the server

```bash
# Create the deploy directories (needs sudo once)
sudo mkdir -p /opt/yoursite /opt/yoursite-staging
sudo chown youruser:youruser /opt/yoursite /opt/yoursite-staging
```

Add nginx configs to serve both directories; see `configs/nginx/` for reference configs.

### GitHub Actions secrets

Set these in both a `staging` and a `production` GitHub Actions environment (Settings → Environments):

| Secret | Value |
|--------|-------|
| `GATEKEEPER_HOST` | Your server's public IP |
| `GATEKEEPER_USER` | SSH username |
| `GATEKEEPER_PORT` | SSH port (22 if standard) |
| `GATEKEEPER_SSH_KEY` | Private key for the deploy user |

The deploy user's public key must be in `~/.ssh/authorized_keys` on the server.

### Workflows

| Workflow | Trigger | Target |
|----------|---------|--------|
| **Test** | Pull request to `main` | Runs `npm ci && npm run build` |
| **Deploy Staging** | Push to `main` or manual dispatch | `staging_path` in config |
| **Deploy Production** | Manual dispatch, requires version tag + typing `deploy` to confirm | `prod_path` in config |

### Manual deploy

```bash
# Staging
gh workflow run deploy-staging.yml

# Production (via GitHub UI or CLI)
gh workflow run deploy-prod.yml -f version=v1.0.0 -f confirm=deploy
```

### LLM tools (optional)

Set `LLM_API_KEY` in your environment for the resume and social tools:

```bash
export LLM_API_KEY=your-key-here
```

Any OpenAI-compatible endpoint works: llama.cpp, Ollama, OpenAI, etc. Configure the URL and model in `data/config.yaml` under `llm:`.
