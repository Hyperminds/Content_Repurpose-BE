# Workspace Control — Production Deployment Guide

Infrastructure for the always-on workspace control plane (wake / shutdown /
status) fronting the EC2 instance. **Do not treat this as auto-deploying** — it
is deployment-ready IaC to be applied deliberately.

- `infra/template.yaml` — the **SAM template** (expands to CloudFormation).
- `infra/openapi.yaml` — API contract.
- Handlers/validation/responses/logging — `app/aws/serverless/`.

> **CloudFormation vs SAM:** SAM *is* CloudFormation — `AWS::Serverless::*`
> resources are expanded by the `AWS::Serverless-2016-10-31` transform into
> native CloudFormation at deploy time. `sam build && sam deploy` runs the
> transform for you; `sam package` + `aws cloudformation deploy` is the raw-CFN
> path if you prefer the CloudFormation CLI.

---

## 1. What gets created (21 resources)

| Area | Resources |
|------|-----------|
| Compute | `WakeFunction`, `ShutdownFunction`, `StatusFunction` (Lambda, arm64, py3.11, X-Ray) |
| API | `WorkspaceApi` (HTTP API) with CORS, throttling, access logs |
| IAM | `WorkspaceControlRole` (least-privilege), `SchedulerInvokeRole` |
| Async / resilience | `WorkspaceControlDLQ` (SQS, SSE), Lambda `EventInvokeConfig` retries |
| Scheduling | `IdleShutdownSchedule` (EventBridge Scheduler, **DISABLED by default**) |
| Observability | 7 CloudWatch alarms + `AlarmTopic` (SNS), log groups w/ retention |

---

## 2. Environment Variables

| Variable | Source | Notes |
|----------|--------|-------|
| `INSTANCE_ID` | template param → Lambda env | Required. `i-…`. |
| `AWS_REGION` | Lambda runtime | Auto-set by AWS — do not override. |
| `CORS_ALLOW_ORIGIN` | template param | Set to the exact frontend URL in prod (not `*`). |
| `POWER_CONTROLLER` | template (const `ec2`) | Selects the EC2 strategy. |
| `APP_ENV` | template (const `production`) | JSON structured logs. |
| Credentials | **IAM role** | Never set keys — default provider chain. |

Tunables (SDK retry/timeout, read by `EnvAWSConfigurationProvider`): `AWS_MAX_ATTEMPTS`,
`AWS_RETRY_MODE`, `AWS_CONNECT_TIMEOUT`, `AWS_READ_TIMEOUT`.

---

## 3. IAM — Least Privilege

**Lambda execution role** (`WorkspaceControlRole`):
- `ec2:StartInstances`, `ec2:StopInstances` — scoped to the **single instance ARN**.
- `ec2:DescribeInstances` — `*` (no resource-level scoping in AWS), constrained by
  an `aws:RequestedRegion` condition.
- `sqs:SendMessage` — DLQ only.
- Managed: `AWSLambdaBasicExecutionRole` (CloudWatch Logs), `AWSXRayDaemonWriteAccess`.

**Scheduler role** (`SchedulerInvokeRole`): `lambda:InvokeFunction` on the shutdown
function only; assume-role locked to `scheduler.amazonaws.com` + `aws:SourceAccount`.

No `ec2:*`, no wildcards on start/stop, no cross-region, no PassRole.

---

## 4. Retry Policies

- **In-SDK (boto3):** adaptive retries + connect/read timeouts (from
  `RetryPolicy`/`TimeoutPolicy`) — handles throttling transparently.
- **Async invocation (Scheduler → shutdown):** Lambda `EventInvokeConfig`
  (`MaximumRetryAttempts: 2`, `MaximumEventAgeInSeconds: 3600`) → on final failure
  routes to the **DLQ**.
- **Scheduler:** `RetryPolicy` (3 attempts, 1h max age) + DLQ.
- **API Gateway (sync):** no server-side retry (correct — the frontend health
  poller owns retries); throttling protects the backend.

---

## 5. Monitoring

CloudWatch alarms → SNS (`AlarmTopic`, optional email via `AlarmEmail`):
- Lambda `Errors` (wake / shutdown / status).
- Lambda `Throttles`.
- SQS DLQ `ApproximateNumberOfMessagesVisible >= 1` (any failed invocation).
- API Gateway `5xx` and p99 `Latency`.

Plus: X-Ray tracing on all functions, JSON access logs, JSON app logs (request-id
correlated). Suggested next step: a CloudWatch dashboard sourcing these metrics.

---

## 6. Security Recommendations (read before go-live)

1. **AUTHENTICATION (blocker).** The HTTP API is currently open. Attach a **JWT
   authorizer** (Cognito / your IdP) or a **Lambda authorizer** — these endpoints
   start/stop infrastructure and must never be public. Wake may need a lighter
   auth (it's the pre-login waker) but should still be protected (signed token /
   API key + rate limit).
2. **CORS.** Set `CorsAllowOrigin` to the exact frontend origin; never `*` with
   credentials.
3. **Scheduler stays DISABLED** until its target is decision-gated (§8).
4. **DLQ encryption** enabled (SSE). Consider a CMK if org policy requires.
5. **Least privilege** already applied; review the instance ARN on each deploy.
6. Enable **AWS Config / CloudTrail** to audit start/stop actions.

---

## 7. Deployment Checklist

**Pre-deploy**
- [ ] `boto3` in `requirements.txt` (present).
- [ ] Confirm the target `InstanceId` and region.
- [ ] Decide `CorsAllowOrigin` (frontend URL).
- [ ] Authorizer decision made (§6.1).
- [ ] `sam validate -t infra/template.yaml` passes.

**Deploy**
```bash
cd server
sam build -t infra/template.yaml
sam deploy --guided \
  --stack-name trendzzo-workspace-control \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-south-1 \
  --parameter-overrides \
      InstanceId=i-0219c11f642a8d920 \
      CorsAllowOrigin=https://trendzzo.hyperminds.tech \
      AlarmEmail=ops@hyperminds.tech
```

**Post-deploy**
- [ ] Confirm the SNS email subscription.
- [ ] `GET {ApiBaseUrl}/workspace/status` → returns instance status.
- [ ] `POST /workspace/wake` on a stopped instance → `starting`, instance enters `pending`.
- [ ] After boot, `POST /workspace/shutdown` → `stopping`.
- [ ] Force an error (bad instance id) → alarm fires → SNS email.
- [ ] Verify JSON logs + X-Ray traces in CloudWatch.

---

## 8. Rollback Plan

- **Full rollback:** `aws cloudformation delete-stack --stack-name
  trendzzo-workspace-control` (or `sam delete`). All resources are in this one
  stack, so teardown is atomic. **Note:** the target EC2 instance is *not* part of
  the stack and is unaffected.
- **Failed deploy:** CloudFormation auto-rolls-back to the last good state on
  `CREATE`/`UPDATE` failure. Keep the previous change set; re-deploy the prior
  template revision if needed.
- **Kill switch (no teardown):**
  - Disable the schedule: set `SchedulerState=DISABLED` (default) and redeploy, or
    `aws scheduler update-schedule --name trendzzo-workspace-idle-shutdown --state DISABLED`.
  - Throttle the API to zero (`ApiThrottleRateLimit=0`) to stop all control calls.
- **Data safety:** drain the DLQ before delete if failed events need triage
  (`aws sqs receive-message`).

---

## 9. Known Follow-ups

- **Decision-gate the scheduled shutdown.** The `shutdown_handler` currently
  stops unconditionally. Before enabling `IdleShutdownSchedule`, the scheduled
  target must consult the idle decision engine (activity / WS / jobs) so it never
  stops a busy instance. Options: a dedicated `idle_check_handler` that only calls
  `shutdown()` when the decision says sleep, or move the check into the handler
  for scheduler-sourced events.
- Add a CloudWatch dashboard + composite alarm.
- Add an authorizer (§6.1) — required for production.
