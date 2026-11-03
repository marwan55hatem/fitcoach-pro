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
npm run e2e        # Playwright on a phone viewport: onboarding, a full workout, Today
```
Run the API too (section 2). The e2e tests use your local database and delete the accounts they create. They need the exercise library seeded and, the first time, `npx playwright install chromium`. When nothing runs on port 8001, Playwright starts the API itself with a higher sign-in rate limit, because every test creates an account; an API you started yourself keeps the normal limit of 10 a minute.

How the web app is put together:
- **Languages:** every page lives under `/en` or `/ar`. Arabic is Egyptian Arabic, laid out right to left. `/` picks your saved choice, otherwise your browser's language. The strings are in `lib/i18n/en.ts` and `lib/i18n/ar.ts`, and TypeScript rejects the Arabic file if a key is missing.
- **Login (backend-for-frontend):** the browser talks only to this app. `app/api/auth/*` stores the refresh token in an httpOnly cookie, and the short-lived access token stays in memory: never in localStorage. `app/api/backend/*` passes API calls on to `API_URL`, along with the caller's IP so the API's rate limit applies per user (`TRUSTED_PROXY_IPS` on the API).
- **Training in the gym:** Train shows the week; a workout (`/train/workout`) has a card per exercise with demo photos, steps, muscles, last time, your best, a Swap button and the set rows. Ticking a set starts the rest timer (the program's rest time, else 90 s), which beeps and vibrates at zero. Set changes go through a queue (`lib/workout/set-queue.ts`): with no signal they wait and retry, and the page warns before closing while something is unsaved. The queue is kept in memory only, never in browser storage, because workout data is health data.
- **Installable app:** `app/manifest.ts`, icons in `public/icons` (made by `node scripts/make-icons.mjs`) and a hand-written `public/sw.js`. The service worker only caches the app shell and an offline page, never API data, and it is registered in production builds only.

**Testing on your phone:** the camera, installing the app and push notifications all need HTTPS, and your phone can't reach `localhost`. Either run `npx next dev --port 3001 --experimental-https` and open `https://<your-PC-IP>:3001` on the same Wi-Fi (accept the certificate warning), or start a tunnel with `cloudflared tunnel --url http://localhost:3001` and open the `https://….trycloudflare.com` address it prints.

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

### Workouts, nutrition and body tracking
All need the `health_data` consent. Dates default to today in the user's timezone and can't be in the future.
- **Workouts:** `POST /sessions` with a `program_day_id` from the active program creates the planned sets, with target reps and last time's weight. Log each set with `PATCH /sessions/{id}/sets/{set_id}`, add extra sets with `POST /sessions/{id}/sets`, then call `POST /sessions/{id}/finish`, which reports new personal bests. Only one workout can be open at a time. Also available: `GET /sessions`, `GET /sessions/open`, `DELETE /sessions/{id}`, `POST /sessions/{id}/swap` (the rest of a planned exercise's sets switch to a substitute with the same muscle, the user's equipment and nothing their injuries rule out; the program itself is unchanged), `GET /exercises/{id}/last` (last time and personal best, from finished workouts only), and `GET /stats/volume?weeks=4` (completed sets per muscle per week).
- **Nutrition** (needs plan access): `GET /nutrition/targets` comes from `app/rules/nutrition.py`, using Mifflin-St Jeor or the latest *confirmed* InBody BMR, the activity factor, the goal and approved adjustments in `nutrition_settings`. `GET /nutrition/day?date=` totals only confirmed meals. Also available: `POST/PATCH/DELETE /nutrition/meals` and `PUT /nutrition/days/{date}/water`. Calorie and macro targets are never returned when the health screening withholds them.
- **Body:** `POST /weight` keeps one weight per day, and `GET /weight` adds a 7-day moving average and the weekly change. `POST /checkins` (sleep, soreness, stress, energy, motivation) keeps one check-in per day, listed by `GET /checkins`.

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
