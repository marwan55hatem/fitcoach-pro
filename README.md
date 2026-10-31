# FitCoach PRO

AI gym coach PWA. See [docs/PROJECT.md](docs/PROJECT.md) for the vision and [CLAUDE.md](CLAUDE.md) for project rules.

Monorepo: `backend/` (FastAPI), `frontend/` (Next.js), `docs/`.

## Prerequisites
Python 3.12, Node 20+, npm, Docker Desktop.

## 1. Infrastructure (PostgreSQL 16 + MinIO)
```powershell
docker compose up -d
```
- Postgres: `localhost:5433` (user/password/db: `fitcoach`)
- MinIO S3 API: `localhost:9000`, console: http://localhost:9001 (`minioadmin` / `minioadmin`, local dev only)
- A one-shot `minio-init` service creates the **private** bucket `fitcoach`.

> MinIO's official images are no longer published on Docker Hub/quay, so compose uses the Chainguard builds (`cgr.dev/chainguard/minio` and `minio-client`).

## 2. Backend
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
```
Check: http://localhost:8001/health returns `{"status":"ok"}`.

```powershell
.\.venv\Scripts\python.exe -m pytest                    # tests
.\.venv\Scripts\python.exe -m ruff check .              # lint
.\.venv\Scripts\python.exe -m scripts.minio_smoke_test  # MinIO upload/download + private-bucket check
```

### Database migrations
```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head                          # apply
.\.venv\Scripts\alembic.exe revision --autogenerate -m "msg"   # new migration after model changes
.\.venv\Scripts\alembic.exe check                                 # fail if models and DB drift
```
Tests create a throwaway `fitcoach_test` database and run the real migrations against it.

## 3. Frontend
```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev        # http://localhost:3001
npm run build      # production build
```
The home page calls `GET {NEXT_PUBLIC_API_URL}/health` and shows the API status.

## Development reference

| Service | Local port |
|---|---|
| API (FastAPI) | 8001 |
| Web (Next.js) | 3001 |
| PostgreSQL | 5433 |
| MinIO S3 / console | 9000 / 9001 |

These avoid the common 3000/8000/5432 so FitCoach can run next to other projects.

- **Tests:** `cd backend; .\.venv\Scripts\python.exe -m pytest` (needs `docker compose up -d`; they use a throwaway `fitcoach_test` database and a `fitcoach-test` bucket, never your dev data).
- **Before committing:** `ruff check .`, `ruff format --check .`, `alembic check`, `pytest`; in `frontend/`: `npm run lint` and `npm run build`. CI (`.github/workflows/ci.yml`) runs the same commands.
- **Migrations:** change a model, run `alembic revision --autogenerate -m "msg"`, review the generated file, then `alembic upgrade head`. Never edit a migration that was already pushed.
- **Sample photos:** put real InBody or meal photos only in `docs/samples/` (gitignored). They are health data and must never be committed.

### Exercise library
Import the open exercise library (876 exercises with demo images, public domain, see [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md)) into your local database and MinIO bucket. Safe to re-run:
```powershell
cd backend
.\.venv\Scripts\python.exe -m app.seed.exercises --check-plan
```
The first run downloads about 100 MB into `backend/.cache/` (gitignored). `--from-dir DIR` imports from a local copy, `--no-images` skips images, `--limit N` imports a sample. `--check-plan` reports whether the default 4-day split is fully covered.

### AI (Groq by default)
All AI calls go through `backend/app/ai/client.py`. `AI_PROVIDER` picks the vendor: `groq` (default) or `openai`. Put the matching key in `backend/.env` (gitignored):
```
GROQ_API_KEY=your-key
```
Then verify the wiring (never prints the key, writes nothing to the database):
```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ai.check
```
It confirms your key can use the configured models, then tests strict JSON, a tool call with replay, streaming, and reading a generated image into strict JSON. It prints token usage and estimated cost for each.

- Models per task are settings: `AI_MODEL_MAIN` (coach, programs), `AI_MODEL_VISION` (InBody and food photos), `AI_MODEL_FAST` (classification). Empty means the default in `app/ai/models.py`.
- Each user has a daily token budget (`AI_DAILY_TOKEN_BUDGET`, default 50000 input + output tokens). Usage and estimated cost are recorded per user per day in `ai_usage`.
- Groq limits are shared across the whole account (Developer plan: 30 requests/min, 8K tokens/min, 200K tokens/day), which is fine for development but not for real users. See [docs/CAPACITY.md](docs/CAPACITY.md).
- Groq's only self-serve vision model, `qwen/qwen3.8-27b`, is a preview model that can be discontinued at short notice. Re-evaluate before production.
- Privacy: turn on **Zero Data Retention** in the Groq console (Data Controls). OpenAI requests use `store=False`. Both send only a hashed user reference, and prompts, images and answers are never logged. Review the provider's current terms, including model training, before real users upload health data.

### Training programs
`POST /programs/generate` creates a draft: the AI proposes, `app/rules/program_validator.py` checks it (exercises, equipment, injuries, weekly volume, session length, order), and invalid answers get one retry before falling back to a rules-only template. `{"use_ai": false}` skips the AI entirely. Nothing changes until `POST /programs/{id}/accept`, which validates again and archives the previous program. See it with `GET /programs/{id}/preview` and `GET /programs/current`.

### Capacity and load testing
The API sheds load instead of collapsing: at most `MAX_INFLIGHT_REQUESTS` (default 40, never above the DB pool capacity) requests run per worker; the rest get a fast `503` with `Retry-After`. Measured numbers, what 1 million users would take, and the scale-out plan are in [docs/CAPACITY.md](docs/CAPACITY.md).

Load test a running server (never production), ideally from a second machine:
```powershell
cd backend
$env:RATE_LIMIT_AUTH="10000000/minute"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8011
# in another terminal:
.\.venv\Scripts\python.exe -m scripts.loadtest http://localhost:8011 "my label" 12
```

### Deleting an account
`DELETE /me` is irreversible and requires the current password in the body:
```powershell
Invoke-RestMethod http://localhost:8001/me -Method Delete -Headers @{Authorization="Bearer $token"} `
  -ContentType application/json -Body '{"password":"your-password"}'
```

## Configuration
Copy `.env.example` files to `.env` (backend) / `.env.local` (frontend). Never commit real secrets.
