# Social Publishing Engine — Production Deployment Checklist

## Required Environment Variables

| Variable | Required In | Purpose |
|----------|-------------|---------|
| `SP_CREDENTIAL_ENCRYPTION_KEY` | Production | Fernet key for token encryption. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `LINKEDIN_CLIENT_ID` | All | LinkedIn OAuth app ID |
| `LINKEDIN_CLIENT_SECRET` | All | LinkedIn OAuth app secret |
| `FACEBOOK_APP_ID` | All | Facebook/Meta OAuth app ID |
| `FACEBOOK_APP_SECRET` | All | Facebook/Meta OAuth app secret |
| `INSTAGRAM_APP_ID` | All | Instagram OAuth app ID |
| `INSTAGRAM_APP_SECRET` | All | Instagram OAuth app secret |
| `MONGODB_URL` | All | MongoDB Atlas connection string |
| `APP_ENV` | All | `development` / `staging` / `production` |
| `FRONTEND_URL` | All | Frontend URL for OAuth callback redirects |

## Pre-Deployment Validation

1. Ensure `SP_CREDENTIAL_ENCRYPTION_KEY` is set (app will fail fast if missing in production)
2. Ensure MongoDB Atlas is accessible from deployment environment
3. Verify OAuth redirect URIs are registered in each platform's developer portal:
   - LinkedIn: `https://{backend}/social-publishing/callback/linkedin`
   - Facebook: `https://{backend}/social-publishing/callback/meta`
   - Instagram: `https://{backend}/social-publishing/callback/instagram`
4. Ensure App Review is completed for each platform (required for third-party users)

## MongoDB Collections Created

| Collection | Purpose |
|------------|---------|
| `sp_social_accounts` | Connected social accounts (encrypted credentials) |
| `sp_social_posts` | Scheduled and published posts |
| `sp_publishing_jobs` | Legacy audit trail of publish attempts |
| `sp_oauth_states` | OAuth CSRF tokens (TTL auto-cleanup) |
| `sp_job_queue` | Production job queue (atomic claiming) |
| `sp_plans` | AI agent publishing plans |
| `sp_rate_limits` | Daily per-platform publishing counters |

All indexes are created automatically on startup via `init_db()`.

## Background Workers Started at Boot

| Worker | Interval | Purpose |
|--------|----------|---------|
| JobScheduler | 30s | Discovers due SCHEDULED posts, creates jobs |
| JobScheduler recovery | 120s | Releases stuck/crashed jobs |
| PublishingWorker | 5s idle | Claims and executes jobs |
| TokenRefreshWorker | 15min | Refreshes tokens expiring within 24h |
| RetryWorker (legacy) | 60s | Retry handler for old publishing system |

## Health Checks

- `GET /health` — returns MongoDB, scheduler, AI service, and WebSocket status
- `GET /social-publishing/metrics` — job queue stats (requires auth)
- `GET /social-publishing/rate-limits` — per-platform daily usage (requires auth)

## Security Checklist

- [ ] `SP_CREDENTIAL_ENCRYPTION_KEY` is unique per environment (not shared between staging/prod)
- [ ] OAuth client secrets are not in source control
- [ ] MongoDB connection string uses TLS (`+srv` or explicit `tls=true`)
- [ ] OAuth redirect URIs use HTTPS in production
- [ ] `APP_ENV=production` disables `/docs` and `/redoc` endpoints
- [ ] CORS origins are restricted (not `*`) in production

## Rollback Plan

The Social Publishing Engine is additive — it adds new collections and endpoints without modifying existing ones. To rollback:

1. Remove the three router registrations from `main.py` (social_publishing_router, social_publishing_oauth_router, social_publishing_agent_router)
2. Remove the worker startup code from the lifespan
3. The `sp_*` collections can be left in place (unused) or dropped

Existing TrendZZo functionality is unaffected.

## Monitoring

- Structured logs: `[INFO] Job completed`, `[WARNING] Job failed — will retry`, `[ERROR] Job permanently failed`
- Metrics endpoint: `GET /social-publishing/metrics` returns jobs_created/processed/succeeded/failed/retried
- WebSocket events: `social_publish_success`, `social_publish_failed`, `social_publish_retrying`

## Known Limitations

- Token refresh requires platform support (LinkedIn refresh is partner-only; Instagram uses token-as-refresh)
- Agent plans are stored in MongoDB but campaign content is regenerated each time (not cached)
- Instagram requires JPEG images only (PNG/WebP rejected)
- Facebook publishing is Page-only (personal profiles not supported by API)
- Rate limits are conservative client-side estimates (platforms don't publish exact limits)
