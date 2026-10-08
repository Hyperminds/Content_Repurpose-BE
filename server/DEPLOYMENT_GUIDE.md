# TrendZZo Backend — Production Deployment Guide

Give this to whoever deploys the backend. It explains every environment variable
to set, the extra install step the browser-publishing feature needs, and how to
verify the deploy worked.

Backend app entrypoint: `app.main:app` (FastAPI / uvicorn)
Health endpoint: `GET /health`
Production frontend origin: `https://trendzzo.hyperminds.tech`
Production backend origin: `https://trendzzo-be.xyz-app.com`

---

## 1. Environment variables (REQUIRED)

Fill every `<placeholder>` from the template file `app/.env.production.template`
into the host's secret store (NOT committed to git). Do **not** copy the local
`app/.env` — it holds development values that are wrong for production.

### These crash the app on startup if missing/wrong (likely cause of a 502):

| Variable | Value | Why |
|---|---|---|
| `APP_ENV` | `production` | Enables real APIs + strict startup checks |
| `USE_MOCK_DATA` | `false` | Real AI generation, not mock content |
| `MONGODB_URL` | Atlas URI (NOT localhost) | App refuses to start if this is localhost in prod |
| `JWT_SECRET` | strong random secret | Signs login tokens. Changing it invalidates all existing logins |
| `SP_CREDENTIAL_ENCRYPTION_KEY` | a Fernet key (see below) | Encrypts stored social tokens. **Required in prod — missing it stops the app from booting** |
| `CORS_ORIGINS` | `https://trendzzo.hyperminds.tech` | Browser blocks the frontend without this |
| `FRONTEND_URL` | `https://trendzzo.hyperminds.tech` | Used for OAuth redirects + emails |

Generate the Fernet key ONCE and never change it (changing it makes already-stored
social credentials undecryptable):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Also configure (features degrade without them):

- AI: `OPENROUTER_API_KEY`, `AI_MODEL`, `OPENROUTER_IMAGE_MODEL`, `ENABLE_AI_IMAGE_GENERATION=true`
- Email (OTP/notifications): `RESEND_API_KEY` + `RESEND_FROM_EMAIL` (preferred), or `SMTP_EMAIL` + `SMTP_PASSWORD`
- Media storage: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`, `STORAGE_BACKEND=cloudinary`
- OAuth (per platform in use): `LINKEDIN_*`, `TWITTER_*` (redirect URIs point at the prod backend origin)

---

## 2. Install step for browser-based publishing (Instagram / Facebook / Reddit / Quora)

These platforms publish via **Hermes**, which drives a real Chromium browser on
the server using Playwright. **Playwright is intentionally NOT in `requirements.txt`**
(it is commented out), so a plain `pip install -r requirements.txt` does NOT install
it. Posting to these platforms will throw a **500 Internal Server Error** until this
is done on the server:

```bash
pip install "playwright>=1.44.0"
python -m playwright install chromium
python -m playwright install-deps      # OS libraries Chromium needs (Linux)
```

Required env for Hermes (already in the template):

- `ENABLE_HERMES_PROVIDER=true`
- `ENABLE_HERMES_INSTAGRAM=true` / `ENABLE_HERMES_FACEBOOK=true` / `ENABLE_HERMES_QUORA=true` (per platform wanted)
- `HERMES_HEADLESS=true`  ← **MUST be true on a headless server**, else Chromium can't start
- `HERMES_BROWSER_PROFILE_DIR=/var/lib/trendzzo/hermes/profiles`  ← a path the app user can WRITE to; create it first

> Operational note: Hermes logs each social account into Instagram/etc. using a
> persistent server-side browser profile. An account must be logged into that
> profile once before posting works. If running headful browser automation isn't
> viable on this host, these platforms won't publish even with Playwright installed.
> (Native OAuth platforms like LinkedIn/Twitter do NOT need Playwright.)

---

## 3. Install & run

```bash
pip install -r app/requirements.txt
# then the Playwright step in section 2 if browser publishing is enabled
uvicorn app.main:app --host 0.0.0.0 --port <port>
```

Run a single uvicorn worker (the pending-confirmation cache is in-process; multiple
workers can cause confirm steps to miss the prepared session).

---

## 4. Verify the deploy

1. `GET https://trendzzo-be.xyz-app.com/health` → should return 200 (not 502).
   A 502 means the process didn't boot — check logs for a startup error naming the
   missing variable (usually `SP_CREDENTIAL_ENCRYPTION_KEY` or a localhost `MONGODB_URL`).
2. Log in from the frontend → should succeed (not 401). A 401 after a correct login
   usually means `JWT_SECRET` differs from what existing tokens were signed with;
   users created before the env was set must log in fresh.
3. Post to a native platform (LinkedIn) → confirms AI + storage + OAuth.
4. Post to Instagram → confirms the Playwright/Hermes install from section 2.

---

## 5. Security — rotate exposed secrets

Any secret shared during development should be rotated before/after go-live and set
fresh in the prod secret store: OpenRouter key, MongoDB credentials, JWT secret,
SMTP/Gmail app password, and the LinkedIn / Twitter / Cloudinary / AWS secrets.
Never commit real secrets to git.
