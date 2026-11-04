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
npm run e2e        # Playwright on a phone viewport: onboarding, workout, Today, Food, Progress
```
Run the API too (section 2). The e2e tests use your local database and delete the accounts they create. They need the exercise library seeded and, the first time, `npx playwright install chromium`. Playwright always starts its own API on port 8001, so stop yours first. That API uses the offline demo AI (`AI_PROVIDER=demo`), so tests never call a real model or spend money. It also has a higher sign-in limit, because every test creates an account.

How the web app is put together:
- **Languages:** every page lives under `/en` or `/ar`. Arabic is Egyptian Arabic, laid out right to left. `/` picks your saved choice, otherwise your browser's language. The strings are in `lib/i18n/en.ts` and `lib/i18n/ar.ts`, and TypeScript rejects the Arabic file if a key is missing.
- **Login (backend-for-frontend):** the browser talks only to this app. `app/api/auth/*` stores the refresh token in an httpOnly cookie, and the short-lived access token stays in memory: never in localStorage. `app/api/backend/*` passes API calls on to `API_URL`, along with the caller's IP so the API's rate limit applies per user (`TRUSTED_PROXY_IPS` on the API).
- **Food:** the six meal slots for a day, with calorie and protein rings against the targets, the day's range when estimates are involved, and water cups (250 ml each). Add a meal by photo (the phone camera opens), by describing it, or from My foods. An AI estimate shows as a draft marked unsaved, with its items, ranges, confidence and questions. Change a portion with − and +, then save it. Only saved meals count.
- **Progress:** weight with its 7-day average, and InBody scans. A scan is uploaded, read by the AI into a draft, checked in a table where doubtful values are highlighted, then confirmed. The muscle, fat and body-fat charts come with the rules' explanation of the last change. Also: progress photos by view with a before/after slider (private, never sent to an AI), weekly sets per muscle against your level's range, and personal bests.
- **Settings:** language, the optional permissions (AI coaching, body photos, reminders), log out and delete account.
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
All AI calls go through `backend/app/ai/client.py`. `AI_PROVIDER` picks the vendor: `groq` (default) or `openai`. `demo` is an offline stand-in with fixed answers, for local demos and the browser tests. It is refused unless `APP_ENV=development`. Put the matching key in `backend/.env` (gitignored):
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
- **Workouts:** `POST /sessions` with a `program_day_id` from the active program creates the planned sets, with target reps and last time's weight. Log each set with `PATCH /sessions/{id}/sets/{set_id}`, add extra sets with `POST /sessions/{id}/sets`, then call `POST /sessions/{id}/finish`, which reports new personal bests. Only one workout can be open at a time. Also available: `GET /sessions`, `GET /sessions/open`, `DELETE /sessions/{id}`, `POST /sessions/{id}/swap` (the rest of a planned exercise's sets switch to a substitute with the same muscle, the user's equipment and nothing their injuries rule out; the program itself is unchanged), `GET /exercises/{id}/last` (last time and personal best, from finished workouts only), `GET /stats/volume?weeks=4` (completed sets per muscle per week) and `GET /stats/records` (personal bests per exercise).
- **Progress photos** (need the `body_photos` consent): upload with `POST /files?kind=body_photo`, then `POST /progress-photos {"file_id", "view": "front"|"side"|"back"}`. A second photo of the same view on the same day replaces the first. `GET /progress-photos` returns signed links for each photo and its thumbnail, and `DELETE /progress-photos/{id}` removes the photo and its file. They are never sent to an AI.
- **Nutrition** (needs plan access): `GET /nutrition/targets` comes from `app/rules/nutrition.py`, using Mifflin-St Jeor or the latest *confirmed* InBody BMR, the activity factor, the goal and approved adjustments in `nutrition_settings`. `GET /nutrition/day?date=` totals only confirmed meals. Also available: `POST/PATCH/DELETE /nutrition/meals` and `PUT /nutrition/days/{date}/water`. Calorie and macro targets are never returned when the health screening withholds them.
- **Body:** `POST /weight` keeps one weight per day, and `GET /weight` adds a 7-day moving average and the weekly change. `POST /checkins` (sleep, soreness, stress, energy, motivation) keeps one check-in per day, listed by `GET /checkins`.

### Photos and files
`POST /files?kind=inbody|body_photo|food|chat` takes one photo as the raw request body. The type is read from the file's content, never its name or Content-Type: JPEG, PNG, WebP or HEIC, plus PDF for InBody scans (the first page is kept). Photos are up to 12 MB and PDFs up to 10 MB, and the limit is enforced while the upload is read. Each photo is turned upright and re-encoded as a JPEG of at most 2000 px with no metadata (GPS and camera tags are removed), plus a 400 px thumbnail. Both are stored privately under `users/<id>/<kind>/`.
- **Consent:** InBody and food photos need `health_data`, body photos need `body_photos`, and chat images need `ai_processing`. Uploads are rate limited (`RATE_LIMIT_UPLOAD`, default 30 a minute).
- **Viewing:** `GET /files/{id}/url` returns signed links to the photo and its thumbnail that expire after 5 minutes. The bucket itself is never public.
- **Deleting:** `DELETE /files/{id}` removes both objects. Meals and scans that used the photo are kept without it, and a body-photo entry goes with its photo. If storage fails mid-way, the file is already hidden and `purge_deleted` finishes the job.
- **Your data:** the export (`GET /me/export`) includes the originals, not the thumbnails. Deleting the account removes both.
- **AI:** `app/services/files.py:load_for_ai` gives a feature a size-limited copy of a photo for an AI call. It refuses body photos, which never go to an AI provider.

### InBody scans
Upload the scan with `POST /files?kind=inbody` (a photo or PDF), then `POST /inbody/analyze {"file_id": ...}`. This needs the `health_data` and `ai_processing` consents. Only the image goes to the AI (`app/ai/inbody.py`): no name, email or profile. The AI copies the printed numbers with a confidence for each, and any text on the sheet is treated as data.
- **Checks:** `app/rules/inbody_validation.py` checks the result:
  - body fat between 3 and 60 %
  - muscle less than weight, and muscle plus fat less than weight
  - BMI matching height and weight
  - body fat % matching fat mass
  - BMR between 1000 and 3500 kcal

  Doubtful values come back in `flags` with lower confidence. Impossible ones are left out.
- **Drafts:** the result is a draft. Fix it with `PUT /inbody/{id}`, then `POST /inbody/{id}/confirm`. Only confirmed scans count anywhere else, for example the BMR in nutrition targets. Unconfirmed drafts are deleted after 7 days.
- **Reading scans:** `GET /inbody` lists confirmed scans, `GET /inbody/drafts` lists drafts, and `GET /inbody/trend` shows weight, muscle and fat over time. The trend also explains the last change, for example "muscle up, fat flat: a good lean gain".

### Food logging
`POST /food/estimate {"file_id" | "text", "slot", "date"?}` estimates a meal from a food photo (uploaded with `kind=food`), a description, or both. It needs the `health_data` and `ai_processing` consents. The prompt (`app/ai/food.py`) knows common Egyptian dishes and portions (koshari, foul, taameya, fatta, molokhia, mahshi, kofta, shawerma, baladi bread, feteer, rice, chicken, tuna, zabadi).
- **Estimate:** each item gets a portion, a calorie range and macros, plus questions when the portion is unclear. Only the photo and your own words about the meal are sent, and text in either is treated as data.
- **Checks:** `app/rules/food_validation.py` checks that calories roughly match protein, carbs and fat (4/4/9), that each range is in order, and that no single item is impossibly large.
- **The result is a draft:** `PUT /food/logs/{id}` saves your checked version and confirms it. If you changed anything, the meal is marked as edited. Drafts left unconfirmed are deleted after 24 hours.
- **Totals:** `/nutrition/day` counts only confirmed meals. An estimate counts its midpoint, and `kcal_min`..`kcal_max` shows the day's possible range.
- **Manual and saved meals:** `POST /food/logs` logs a meal you type in. `POST /food/personal {"meal_log_id"}` keeps a confirmed meal as one of your own foods, and `GET /food/personal` lists them.

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
