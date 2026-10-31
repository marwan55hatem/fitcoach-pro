# FitCoach PRO

## Vision
FitCoach PRO is an AI gym coach delivered as an installable PWA. It aims to replace a human coach for day-to-day coaching: building and adapting programs, answering questions, analyzing body composition and progress, tracking food, and keeping the user accountable with reminders.

Principles:
- **AI proposes, rules validate, user approves.** The AI never changes a program or target on its own.
- **Privacy first.** Health data, InBody scans and body photos are sensitive and stored privately.
- **Safe by design.** No diagnosis or medical treatment; red flags are escalated.

## Stack
- Backend: FastAPI (Python 3.12), PostgreSQL, SQLAlchemy 2, Alembic
- Storage: S3-compatible private object storage (MinIO locally)
- Frontend: Next.js (App Router, TypeScript, Tailwind), installable PWA
- AI: Groq by default (OpenAI also supported) for chat, vision and program personalization, behind a provider interface so another vendor can be added as one adapter

## Features

### 1. Onboarding and health screening
Collects goals, experience, schedule, equipment, injuries and a health questionnaire (PAR-Q style). Screening flags that need professional clearance are escalated before any programming.

### 2. Program generator
The AI proposes a personalized program from the user's profile; the rules engine validates it (volume, frequency, progression, contraindications) and the user approves it.

### 3. Exercise library with demo images
Curated exercises with demo images, muscles worked, equipment, cues and substitutions.

### 4. InBody analysis
Upload an InBody scan; vision extracts the metrics into a **draft** the user reviews and confirms. Trends are tracked over time.

### 5. Progress photos
Private, signed-link-only body photo storage with side-by-side comparison. Photos are never public and can be deleted on request.

### 6. Food logging by photo or text
Meals logged via photo or free text; extracted calories/macros are a **draft** until confirmed.

### 7. Adaptive weekly review
Weekly check-in combining adherence, performance, bodyweight/InBody and nutrition. The AI proposes adjustments; the rules engine validates; the user approves.

### 8. Reminders via Telegram and Web Push
Workout, meal-logging and check-in reminders through Telegram and Web Push notifications.

### 9. Safety and evals
Guardrails against diagnosis/medical prescriptions, red-flag escalation, and prompt-injection resistance (text inside images is data). Covered by an automated eval suite run on changes to prompts or rules.

## Non-goals (for now)
- Medical advice, diagnosis or treatment
- Public sharing of health data or photos
