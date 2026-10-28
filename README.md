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

### Deleting an account
`DELETE /me` is irreversible and requires the current password in the body:
```powershell
Invoke-RestMethod http://localhost:8001/me -Method Delete -Headers @{Authorization="Bearer $token"} `
  -ContentType application/json -Body '{"password":"your-password"}'
```

## Configuration
Copy `.env.example` files to `.env` (backend) / `.env.local` (frontend). Never commit real secrets.
