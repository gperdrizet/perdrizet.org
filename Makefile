# ============================================================
#  perdrizet.org — Project Makefile
#  Common tasks for development, data sync, and deployment.
# ============================================================

.DEFAULT_GOAL := help
.PHONY: help setup dev build sync-projects tailor-resume deploy-staging deploy-prod

# ---- Helpers -----------------------------------------------

help:
	@echo ""
	@echo "  perdrizet.org — Personal Brand Platform"
	@echo ""
	@echo "  Setup"
	@echo "    make setup            Interactive first-time setup wizard"
	@echo ""
	@echo "  Site development"
	@echo "    make dev              Start Astro dev server"
	@echo "    make build            Build static site to site/dist/"
	@echo ""
	@echo "  Data"
	@echo "    make sync-projects    Fetch GitHub repos → data/projects.yaml"
	@echo ""
	@echo "  Resume"
	@echo "    make tailor-resume    Tailor resume to a job posting"
	@echo ""
	@echo "  Deploy (CI/CD does this automatically, but you can run manually)"
	@echo "    make deploy-staging   rsync dist/ to staging on gatekeeper"
	@echo "    make deploy-prod      rsync dist/ to production on gatekeeper"
	@echo ""

# ---- Setup -------------------------------------------------

setup:
	@python3 scripts/setup.py

# ---- Site --------------------------------------------------

dev:
	cd site && npm run dev

build:
	cd site && npm run build

# ---- Data sync ---------------------------------------------

sync-projects:
	@echo "Syncing GitHub repos to data/projects.yaml..."
	@cd tools/github-agent && \
		[ -d .venv ] || python3 -m venv .venv && \
		.venv/bin/pip install -q -r requirements.txt && \
		.venv/bin/python agent.py $(ARGS)

suggest-groups:
	@echo "Asking LLM to suggest group consolidations..."
	@cd tools/github-agent && \
		[ -d .venv ] || python3 -m venv .venv && \
		.venv/bin/pip install -q -r requirements.txt && \
		.venv/bin/python agent.py --suggest-groups

# ---- Admin agent -------------------------------------------

admin:
	@echo "Starting admin agent on http://127.0.0.1:8600 ..."
	@cd tools/admin && \
		[ -d .venv ] || python3 -m venv .venv && \
		.venv/bin/pip install -q -r requirements.txt && \
		.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8600 --reload

# ---- Resume ------------------------------------------------

tailor-resume:
	@echo "Starting resume tailoring..."
	@cd tools/resume && \
		[ -d .venv ] || python3 -m venv .venv && \
		.venv/bin/pip install -q -r requirements.txt && \
		.venv/bin/python tailor.py

# ---- Deploy (manual) ---------------------------------------

deploy-staging: build
	@if [ -z "$$GATEKEEPER_USER" ] || [ -z "$$GATEKEEPER_HOST" ]; then \
		echo "Error: set GATEKEEPER_USER and GATEKEEPER_HOST in .env"; exit 1; \
	fi
	rsync -avz --delete site/dist/ \
		$$GATEKEEPER_USER@$$GATEKEEPER_HOST:/opt/perdrizet.org-staging/

deploy-prod: build
	@if [ -z "$$GATEKEEPER_USER" ] || [ -z "$$GATEKEEPER_HOST" ]; then \
		echo "Error: set GATEKEEPER_USER and GATEKEEPER_HOST in .env"; exit 1; \
	fi
	@read -p "Deploy to PRODUCTION at perdrizet.org? [y/N] " confirm; \
		[ "$$confirm" = "y" ] || { echo "Aborted."; exit 1; }
	rsync -avz --delete site/dist/ \
		$$GATEKEEPER_USER@$$GATEKEEPER_HOST:/opt/perdrizet.org/
