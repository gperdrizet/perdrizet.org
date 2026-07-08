# ============================================================
#  DotProfile — Project Makefile
#  Common tasks for development, data sync, and direct deployment.
# ============================================================

.DEFAULT_GOAL := help
.PHONY: help dev build admin deploy-staging deploy-prod

# ---- Helpers -----------------------------------------------

help:
	@echo ""
	@echo "  DotProfile"
	@echo ""
	@echo "  Site development"
	@echo "    make dev              Start Astro dev server"
	@echo "    make build            Build static site to site/dist/"
	@echo ""
	@echo "  Admin"
	@echo "    make admin            Start admin UI/API (includes GitHub sync tooling)"
	@echo ""
	@echo "  Deploy (direct to VPS)"
	@echo "    make deploy-staging   rsync dist/ to staging on gatekeeper"
	@echo "    make deploy-prod      rsync dist/ to production on gatekeeper"
	@echo ""

# ---- Site --------------------------------------------------

dev:
	cd site && npm run dev

build:
	cd site && npm run build

# ---- Admin agent -------------------------------------------

admin:
	@echo "Starting admin agent on http://127.0.0.1:8600 ..."
	@cd tools/admin && \
		[ -d .venv ] || python3 -m venv .venv && \
		.venv/bin/pip install -q -r requirements.txt && \
		.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8600 --reload
# ---- Deploy (manual) ---------------------------------------

deploy-staging: build
	@if [ -z "$$VPS_USER" ] || [ -z "$$VPS_HOST" ]; then \
		echo "Error: set VPS_USER and VPS_HOST in .env"; exit 1; \
	fi
	rsync -avz --delete site/dist/ \
		$$VPS_USER@$$VPS_HOST:/opt/perdrizet.org-staging/

deploy-prod: build
	@if [ -z "$$VPS_USER" ] || [ -z "$$VPS_HOST" ]; then \
		echo "Error: set VPS_USER and VPS_HOST in .env"; exit 1; \
	fi
	@read -p "Deploy to PRODUCTION at perdrizet.org? [y/N] " confirm; \
		[ "$$confirm" = "y" ] || { echo "Aborted."; exit 1; }
	rsync -avz --delete site/dist/ \
		$$VPS_USER@$$VPS_HOST:/opt/perdrizet.org/
