# TrendZZo — Deployment Documentation

Reference for deploying the TrendZZo backend (FastAPI) and frontend (React/Vite).
**Documentation only — nothing is deployed here.** Two backend topologies are
covered because both exist in the codebase:

- **A. Long-lived container/VM** (current model: EC2 + Nginx, or ECS/Fargate) —
  full feature set including the background scheduler, metering worker, WebSocket
  and SSE.
- **B. Serverless** (prepared via Mangum, see `app/LAMBDA.md`) — HTTP API only;
  scheduling/metering/realtime move to managed services.

External managed dependencies (both models): **MongoDB Atlas**, **OpenRouter**
(AI), **Cloudinary** (image storage), **Gmail SMTP** (OTP/email), **LinkedIn
OAuth**, **Vercel** (frontend hosting).

---

## 1. Required AWS Services

### Model A — Container / VM
| Service | Purpose |
|---------|---------|
| **EC2** or **ECS Fargate** | Runs the FastAPI app (uvicorn). Fargate preferred for autoscaling without managing hosts. |
| **Application Load Balancer (ALB)** | TLS termination, health checks (`/health`), path routing; **WebSocket-capable** listener for `/ws`. |
| **ECR** | Container image registry (if containerized). |
| **ACM** | TLS certificate for the ALB / custom domain. |
| **Route 53** | DNS for the API domain (e.g. `api.trendzzo.hyperminds.tech`). |
| **Secrets Manager** / **SSM Parameter Store** | Store secrets (JWT, Mongo URL, API keys). |
| **CloudWatch** (Logs + Metrics + Alarms) | Logs, metrics, alerting. |
| **S3** (optional) | Build artifacts / static assets / backups. |

### Model B — Serverless (prepared, not yet wired)
| Service | Purpose |
|---------|---------|
| **Lambda** | Runs FastAPI via Mangum (`app.lambda_handler.handler`). |
| **API Gateway (HTTP API v2)** | Front door for HTTP/REST + OpenAPI. |
| **API Gateway (WebSocket API)** | Required for realtime — see `app/WEBSOCKET_ARCHITECTURE.md`. |
| **DynamoDB** | WebSocket connection registry (`connectionId ↔ user_id`). |
| **EventBridge Scheduler** | Replaces the in-process polling scheduler → fires the publish trigger. |
| **SQS / Kinesis Firehose** (optional) | Durable sink for metering events (in-process queue worker is disabled on Lambda). |
| **Secrets Manager**, **CloudWatch**, **Route 53**, **ACM** | As above. |

### Frontend
- **Vercel** hosts the React/Vite app (current). AWS alternative: **S3 + CloudFront**.

---

## 2. Required IAM Permissions

Grant least-privilege roles; never use root or broad `*` policies.

### App execution role (Model A: ECS task role / EC2 instance profile)
- `secretsmanager:GetSecretValue` (scoped to the app's secret ARNs) — read secrets at boot.
- `ssm:GetParameter` / `GetParameters` (if using Parameter Store).
- `logs:CreateLogStream`, `logs:PutLogEvents` (scoped to the app log group).
- `cloudwatch:PutMetricData` (custom metrics, optional).
- **No AWS S3/DB perms needed for MongoDB/Cloudinary/OpenRouter** — those use their own API keys (not IAM).

### Deployment/CI role
- `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`,
  `ecr:InitiateLayerUpload`/`UploadLayerPart`/`CompleteLayerUpload`.
- `ecs:UpdateService`, `ecs:RegisterTaskDefinition`, `ecs:DescribeServices`
  (ECS) **or** EC2/SSM deploy perms.
- `iam:PassRole` (scoped to the task execution role).

### Model B additions (Lambda + API Gateway WebSocket)
- Lambda execution role: `logs:*` (scoped), `secretsmanager:GetSecretValue`.
- `execute-api:ManageConnections` — for `PostToConnection` to push WS messages.
- DynamoDB: `dynamodb:PutItem/GetItem/DeleteItem/Query` (scoped to the
  connections table).
- EventBridge Scheduler role: `scheduler:CreateSchedule/DeleteSchedule` and
  `lambda:InvokeFunction` on the publish target.

---

## 3. Required Environment Variables

> Non-secret config can be plain env vars; **secrets must come from Secrets
> Manager / SSM** (see §4). `app/.env.example` is the source template.

### Backend — core
| Variable | Example | Notes |
|----------|---------|-------|
| `APP_ENV` | `production` | Controls CORS/docs/rate-limit behaviour. |
| `USE_MOCK_DATA` | `false` | `true` = mock AI (no credits). |
| `DB_NAME` | `content_repurposer` | |
| `JWT_ALGORITHM` | `HS256` | |
| `JWT_EXPIRY_MINUTES` | `43200` | 30 days. |
| `CORS_ORIGINS` | `https://trendzzo.hyperminds.tech` | Exact frontend origin (no trailing slash). |
| `FRONTEND_URL` | `https://trendzzo.hyperminds.tech` | Used in OAuth redirects / emails. |
| `LINKEDIN_REDIRECT_URI` | `https://<api-domain>/auth/linkedin/callback` | Must match LinkedIn app config. |
| `AI_MODEL` | `openai/gpt-4o-mini` | Optional override. |

### Backend — connection-pool tuning (added in DB optimization)
| Variable | Default | Notes |
|----------|---------|-------|
| `MONGO_MAX_POOL_SIZE` | `50` | Keep `workers × maxPoolSize` under the Atlas connection cap. On Lambda use ~5–10. |
| `MONGO_MIN_POOL_SIZE` | `5` | |
| `MONGO_MAX_IDLE_MS` | `30000` | |
| `MONGO_WAIT_QUEUE_TIMEOUT_MS` | `5000` | |

### Frontend (Vercel build-time — must rebuild to apply)
| Variable | Example |
|----------|---------|
| `VITE_API_BASE_URL` | `https://<api-domain>` (no trailing slash) |
| `VITE_WS_URL` | `wss://<api-domain>/ws` |
| `VITE_APP_ENV` | `production` |

---

## 4. Secrets

Store **only** in Secrets Manager / SSM SecureString — never in git, never in
plain Lambda/ECS env if avoidable. Rotate on a schedule.

| Secret | Used by | Rotation notes |
|--------|---------|----------------|
| `MONGODB_URL` | DB connection | Contains Atlas user+password. Rotate via Atlas + update secret. |
| `JWT_SECRET` | Token signing/verification | **Rotating invalidates all sessions** — plan a maintenance window; never commit. |
| `OPENROUTER_API_KEY` | AI generation | Rotate in OpenRouter dashboard. |
| `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` | Image uploads | `CLOUDINARY_CLOUD_NAME` is non-secret. |
| `SMTP_EMAIL` / `SMTP_PASSWORD` | OTP / notification email | Gmail **app password**, not the account password. |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | OAuth | |
| `TWITTER_*`, `REDDIT_*`, `MEDIUM_*` | Future OAuth | Currently empty/optional. |

**Practices:** unique secrets per environment (dev/staging/prod); least-privilege
read; audit access via CloudTrail; the app reads secrets at startup (Model A) or
per cold start (Model B).

---

## 5. Required Networking

### Model A
- **ALB** in public subnets; app tasks/instances in **private subnets**.
- **NAT Gateway** so private tasks reach the internet (Atlas, OpenRouter,
  Cloudinary, SMTP, LinkedIn).
- **Security groups:**
  - ALB SG: inbound `443` from internet.
  - App SG: inbound only from the ALB SG on the app port (e.g. `8000`); outbound
    `443` to the internet (and `587`/`465` for SMTP).
- **TLS:** ACM cert on the ALB; redirect `80 → 443`.
- **WebSockets:** the ALB listener/target group must allow upgrade; raise idle
  timeout (e.g. 300s+) so `/ws` and `/events/stream` aren't dropped.
- **Nginx (current EC2):** set `client_max_body_size 15M;` (image uploads up to
  10 MB) and proxy upgrade headers for WebSockets.
- **MongoDB Atlas Network Access:** allow-list the **NAT Gateway / public egress
  IP** (or use VPC peering / PrivateLink). Without this, the app connects locally
  but fails in the VPC.

### Model B (serverless)
- API Gateway is public (HTTPS by default).
- If Lambda runs in a VPC (to reach private resources), attach to private subnets
  **with a NAT** for outbound internet, or use Atlas PrivateLink.
- Atlas allow-list: the NAT egress IP, or PrivateLink.

### CORS
`CORS_ORIGINS` must list the exact frontend origin(s). With a non-`*` value the
app automatically enables credentialed CORS.

---

## 6. Monitoring Requirements

### Health & probes
- **Liveness/Readiness:** `GET /health` returns MongoDB, scheduler, AI, and
  WebSocket status. Wire as the ALB target-group health check (Model A) or an
  external uptime monitor (Model B). Treat `degraded` distinctly from `healthy`.
- Keep an external synthetic monitor hitting `/health` from outside the VPC.

### Logs
- Ship stdout/stderr to **CloudWatch Logs** (ECS awslogs driver / Lambda native /
  CloudWatch agent on EC2). Retain 30–90 days.
- The error-handler middleware emits structured error logs; the metering
  middleware records per-request usage to the `metering_events` collection
  (queryable via `/metering/*`).

### Metrics & alarms (CloudWatch)
- **Infra:** CPU/memory, task/instance count, ALB 5xx rate, ALB target
  unhealthy count, request latency p50/p95/p99.
- **App-level (custom or via metering):** request error rate, AI token spend
  (from `generation_logs` / `/ai-usage`), publish failure count, scheduler
  backlog (posts stuck in `scheduled`/`posting`).
- **Dependencies:** MongoDB Atlas alerts (connections, CPU, disk, slow queries);
  OpenRouter credit balance; Cloudinary quota.
- **Alarms to define:** ALB 5xx > threshold, `/health` failing, p95 latency
  breach, Mongo connection saturation, AI spend spike, publish failure spike.

### Tracing (optional)
- AWS X-Ray (Lambda/ECS) or OpenTelemetry for request tracing across the AI/DB
  calls.

---

## 7. Rollback Strategy

### Principles
- **Immutable, versioned artifacts** (image tags / Lambda versions) — never
  mutate a running release in place.
- **One change at a time** (app vs. config vs. infra) so rollback is unambiguous.

### Model A (ECS / EC2)
- **ECS:** keep the previous **task definition revision**. Roll back by updating
  the service to the prior revision (or use CodeDeploy **blue/green** with
  automatic rollback on alarm). Rollback time = one service update.
- **EC2 + Nginx (current):** deploy by pulling a tagged release; roll back via
  `git checkout <previous-tag>` + restart (pm2/systemd). Keep the previous
  release dir for instant switch.

### Model B (Lambda)
- Publish a new **version** and shift the **alias** with API Gateway. Roll back by
  pointing the alias to the previous version (instant). Use weighted aliases for
  canary; auto-rollback on CloudWatch alarm.

### Database migrations (critical)
- MongoDB is schema-flexible, but **index creation and data backfills are not
  auto-reverted**. Make migrations **backward-compatible** (additive) so an app
  rollback still works against the new indexes.
- Index creation is idempotent (`init_db`); dropping indexes is the only
  destructive step — avoid coupling it to a release.
- Take an **Atlas snapshot** before any data-shape migration; document the
  restore procedure.

### Config / secrets rollback
- Env-var/secret changes are deploys too. Keep previous values; reverting
  `CORS_ORIGINS`, `JWT_SECRET`, or `VITE_API_BASE_URL` requires the same
  redeploy/rebuild discipline (the frontend must be **rebuilt** for `VITE_*`).

### Post-rollback verification checklist
1. `GET /health` → `healthy` (mongodb, scheduler, ai_service, websockets).
2. Auth round-trip (login → authenticated request).
3. One AI generation (or confirm `mock_mode` if intended).
4. One publish/schedule path.
5. CORS clean from the frontend; no 5xx spike on the ALB/API Gateway.

---

## Appendix — Pre-deploy checklist
- [ ] Secrets present in Secrets Manager for the target environment.
- [ ] `APP_ENV=production`, `USE_MOCK_DATA=false`, `OPENROUTER_API_KEY` set.
- [ ] `CORS_ORIGINS` + `FRONTEND_URL` = exact frontend origin.
- [ ] `LINKEDIN_REDIRECT_URI` matches the LinkedIn app and the API domain.
- [ ] `CLOUDINARY_*` set (image uploads persist).
- [ ] Atlas Network Access allow-lists the egress IP / PrivateLink.
- [ ] Nginx/ALB body size ≥ 15 MB and WebSocket upgrade enabled.
- [ ] Frontend rebuilt with correct `VITE_API_BASE_URL` / `VITE_WS_URL`.
- [ ] Index migration run once (`init_db`).
- [ ] CloudWatch alarms + external `/health` monitor active.
- [ ] Previous artifact/version retained for rollback.
