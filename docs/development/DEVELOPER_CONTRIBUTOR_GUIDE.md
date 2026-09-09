# Developer & Contributor Guide

This guide is for developers who want to run, extend, or contribute to `smaterecondata`.

## Scope

Use this guide for:
- local setup
- environment configuration
- development workflow
- testing
- deployment references
- contribution process

If you only want to use the product, start with:
- [SmatEconData](https://github.com/merwanroudane/smaterecondata)
- [localhost:3001/chat](http://localhost:3001/chat)

## Prerequisites

- Node.js 18+ and npm 9+
- Python 3.9+
- OpenRouter API key (required)
- FRED and Comtrade API keys (recommended)

## Local Setup

Clone and run setup script:

Linux/macOS:
```bash
git clone https://github.com/merwanroudane/smaterecondata.git
cd smaterecondata
./scripts/setup.sh
```

Windows PowerShell:
```powershell
git clone https://github.com/merwanroudane/smaterecondata.git
cd smaterecondata
.\scripts\setup.ps1
```

Windows CMD:
```cmd
git clone https://github.com/merwanroudane/smaterecondata.git
cd smaterecondata
scripts\setup.bat
```

## Environment

Create `.env` in repo root and set:

```bash
# Required (LLM_PROVIDER options: openrouter, vllm, ollama, lm-studio)
LLM_PROVIDER=openrouter
LLM_MODEL=openai/gpt-4o-mini
OPENROUTER_API_KEY=sk-or-...
JWT_SECRET=<random-string>

# Recommended
FRED_API_KEY=...
COMTRADE_API_KEY=...

# Optional (Supabase for production-style auth)
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_KEY=...
```

Notes:
- `backend/data/indicators.db` is created if missing.
- `backend/data/faiss_index` is created/rebuilt on demand when vector search is enabled.
- In development, Supabase is optional (mock auth path is available).

## Run Locally

Run both services:
```bash
source backend/.venv/bin/activate
npm run dev
```

Or run separately:
```bash
# Backend
source backend/.venv/bin/activate
npm run dev:backend

# Frontend
npm run dev:frontend
```

Default URLs:
- Frontend: `http://localhost:5173`
- Backend: `http://localhost:3001`
- MCP endpoint: `http://localhost:3001/mcp`

## MCP For Development

Hosted endpoint:
- `http://localhost:3001/mcp`

Local endpoint:
- `http://localhost:3001/mcp`

Codex:
```bash
codex mcp add smatecondata-local --url http://localhost:3001/mcp
codex mcp get smatecondata-local
```

Claude Code:
```bash
claude mcp add --transport sse smatecondata-local http://localhost:3001/mcp --scope user
claude mcp get smatecondata-local
```

## Testing

Backend:
```bash
cd backend
source .venv/bin/activate
python -m unittest discover -s backend/tests
```

Frontend:
```bash
npm run test --workspace=packages/frontend
```

Additional references:
- [Testing Guide](../guides/testing.md)
- [Complex Query Testing](../guides/COMPLEX_QUERY_TESTING.md)

## Architecture At A Glance

`smaterecondata` has:
- FastAPI backend in `backend/`
- React + TypeScript frontend in `packages/frontend/`
- Provider integrations in `backend/providers/`
- Query orchestration and routing in `backend/services/`

Detailed references:
- [LLM Abstraction](LLM_ABSTRACTION.md)
- [Routing Improvements](ROUTING_IMPROVEMENTS.md)
- [Metadata System](METADATA_SYSTEM_IMPROVEMENTS.md)

## Deployment

Primary references:
- [Deployment Summary](../DEPLOYMENT_SUMMARY.md)
- [Apache Pro Mode Setup](../deployment/apache-promode-setup.md)

Production checklist:
1. Generate strong `JWT_SECRET`.
2. Configure `ALLOWED_ORIGINS` for your domain.
3. Set production auth configuration (Supabase keys).
4. Build frontend and run backend with production settings.
5. Put HTTPS reverse proxy in front of backend.

## Contribution Workflow

1. Fork the repository.
2. Create a branch: `git checkout -b feature/<name>`.
3. Make changes with tests and docs updates.
4. Run relevant test suites.
5. Open a pull request to `main`.

More details:
- [Contributors Guide](../../.github/CONTRIBUTORS.md)
- [Security Policy](../../.github/SECURITY.md)
- [Codebase Docs Index](../README.md)
