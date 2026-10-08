# Reddit user-assisted publishing — manual live smoke test

This is the **first real** Hermes platform workflow. It drives a **real browser**
via Playwright and requires a human to sign in and to explicitly confirm the
final publish. There is no simulator and no fake HTTP service in this path.

> **This flow is NOT auto-verified by the test suite.** Automated tests use a
> scripted fake `SemanticBrowser` and never open a real browser or post real
> content. The steps below are the only way to verify true live publishing, and
> they must be run by a person against a real Reddit account.

## What this respects (by design)

- **No stored Reddit passwords.** You log in yourself, in the browser window.
- **No MFA/CAPTCHA bypass.** If Reddit challenges you, you complete it manually.
- **No autonomous posting.** The workflow prepares the post and stops; nothing
  is submitted until you call the explicit `confirm` endpoint.
- **No anti-bot evasion / stealth / proxy rotation.** None is implemented.

---

## 1. Prerequisites

- A **test** Reddit account you are comfortable posting from.
- A subreddit you may post to (e.g. your own profile subreddit
  `u_<yourusername>`, or a personal test subreddit). Do not spam real
  communities.
- The backend running locally with Mongo reachable.

## 2. Install the browser runtime (only needed for this flow)

Playwright is an **optional** dependency and is imported lazily, so the app and
the whole test suite run fine without it. Install it only to run this live test:

```powershell
# from Content_Repurpose-BE/server, with the project venv active
pip install playwright
python -m playwright install chromium
```

## 3. Enable the feature (local/dev only)

Set these in `Content_Repurpose-BE/server/app/.env` (all default to off/safe):

```dotenv
ENABLE_HERMES_PROVIDER=true
HERMES_HEADLESS=false          # headful so you can sign in and watch the post
# optional: a stable profile dir so your Reddit login persists between runs
# HERMES_BROWSER_PROFILE_DIR=C:\Users\<you>\trendzzo_hermes_profiles
```

Leave `HERMES_HEADLESS=false`. A visible window is required so you can complete
sign-in / MFA and see the composed post before confirming.

## 4. Start the backend

```powershell
# from Content_Repurpose-BE/server
uvicorn app.main:app --reload --port 8000
```

You should see the Hermes provider register and the confirmation-expiry worker
start in the logs. With the flag off, the `/social-publishing/reddit/*` routes
return 404.

## 5. Get an auth token

Log in through the app (or your usual dev login) and copy the JWT. Export it for
the curl calls below:

```powershell
$TOKEN = "<paste-jwt-here>"
$BASE  = "http://127.0.0.1:8000"
```

You also need the internal **account id** of a connected Reddit account for the
current tenant (the `account_id` used by the social-accounts API).

## 6. Prepare a post (this opens the browser and STOPS)

```powershell
curl -Method POST "$BASE/social-publishing/reddit/posts" `
  -Headers @{ Authorization = "Bearer $TOKEN" } `
  -ContentType "application/json" `
  -Body '{
    "account_id": "<your-account-id>",
    "subreddit": "u_<yourusername>",
    "title": "Trendzzo smoke test",
    "body": "Hello from a user-assisted Trendzzo publish."
  }'
```

What happens:

1. A Chromium window opens.
2. If you are **not** signed in, it navigates to Reddit login and the response is
   `{"status":"action_required", ... "Reddit login required ..."}`. **Sign in
   (and complete MFA) yourself** in that window, then re-run this same POST.
3. Once signed in, it navigates to `r/<subreddit>/submit`, fills the title/body,
   and waits at the composed post **without clicking Post**.
4. The response is:

   ```json
   {
     "status": "action_required",
     "action": "confirm_reddit_publish",
     "confirmation_id": "<id>",
     "operation_id": "reddit_...",
     "preview": { "subreddit": "...", "title": "...", "body": "...", "media_count": 0 }
   }
   ```

Copy the `confirmation_id`.

> Image post variant: add `"media_paths": ["media_0.png"]` (a single jpg/jpeg/
> png/gif). Video is intentionally **not** supported.

## 7. Confirm and publish (the ONLY step that submits)

```powershell
curl -Method POST "$BASE/social-publishing/reddit/confirm/<confirmation_id>" `
  -Headers @{ Authorization = "Bearer $TOKEN" }
```

- On success: `{"status":"published","external_url":"https://www.reddit.com/r/.../comments/.../","external_post_id":"...","platform":"reddit"}`.
  Open the `external_url` and confirm the post exists.
- If the result is uncertain: `{"status":"unknown", ...}`. This is **not**
  retried automatically — check Reddit manually before doing anything else, to
  avoid duplicate posts.

## 8. Cancel instead (optional)

```powershell
curl -Method POST "$BASE/social-publishing/reddit/cancel/<confirmation_id>" `
  -Headers @{ Authorization = "Bearer $TOKEN" }
```

## 9. List pending confirmations

```powershell
curl "$BASE/social-publishing/reddit/pending" -Headers @{ Authorization = "Bearer $TOKEN" }
```

The frontend Social Accounts page renders the same list with **Publish to
Reddit** / **Cancel** buttons, so you can also drive steps 6–8 from the UI.

## 10. Expiry

If you never confirm, the pending confirmation expires after ~15 minutes
(`ttl_seconds` default 900) and is marked `expired` by the background sweeper. It
is **never** rescheduled or auto-published.

---

## Why fully autonomous Reddit posting is NOT enabled

Reddit posting requires a human in the loop by design:

- Sign-in and MFA are completed by the user in a visible browser; we never type
  or store credentials and never bypass MFA/CAPTCHA.
- The final submit only happens on an explicit `confirm` call — a scheduled job
  surfaces `action_required` instead of posting on its own.
- After the Post click, an uncertain outcome is `UNKNOWN` and is verified rather
  than blindly retried, so a network blip cannot cause a duplicate post.

This keeps the workflow within platform-compliant, user-directed use.

## Turn it back off

Set `ENABLE_HERMES_PROVIDER=false` (or remove the line) and restart. The routes
return 404 again and the native LinkedIn/Meta/X publishing path is entirely
unaffected either way.
