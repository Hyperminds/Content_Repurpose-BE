# Workspace Control — Serverless Infrastructure

Always-on control plane for the workspace EC2 instance. It can start/stop/query
the instance **even when the main FastAPI backend is stopped** (the "external
waker"). Handlers reuse the shared `EC2PowerController` — no business-logic
duplication.

## Endpoints
| Method | Path                  | Lambda            | Returns            |
|--------|-----------------------|-------------------|--------------------|
| POST   | `/workspace/wake`     | `wake_handler`    | `{"status":"starting"}` |
| POST   | `/workspace/shutdown` | `shutdown_handler`| `{"status":"stopping"}` |
| GET    | `/workspace/status`   | `status_handler`  | instance status    |

## Components
- **Handlers** — `app/aws/serverless/handlers.py` (thin; delegate to `EC2PowerController`).
- **Validation** — `request_validation.py` (typed request model, 400 on bad input).
- **Responses** — `responses.py` (API Gateway proxy shape + CORS on every response).
- **Logging** — `logging_config.py` (JSON → CloudWatch, request-scoped context).
- **API Gateway + CORS** — `infra/template.yaml` (HTTP API with `CorsConfiguration`).
- **DLQ** — `WorkspaceControlDLQ` (SQS) attached to every function's `DeadLetterQueue`.
- **IAM** — least-privilege: `ec2:Start/StopInstances` scoped to the instance ARN,
  `ec2:DescribeInstances` (`*`, no resource scoping), `sqs:SendMessage` to the DLQ,
  basic execution (CloudWatch Logs).
- **OpenAPI** — `infra/openapi.yaml`.

## Configuration (env)
- `INSTANCE_ID` (required) — target EC2 instance id.
- `AWS_REGION` — provided automatically by the Lambda runtime.
- `CORS_ALLOW_ORIGIN` — set to the frontend URL in production.
- Credentials: none — resolved from the function's **IAM role**.

## Deploy (not run here)
```bash
cd server
sam build -t infra/template.yaml
sam deploy --guided \
  --parameter-overrides InstanceId=i-0abc123def456789 CorsAllowOrigin=https://trendzzo.hyperminds.tech
```

## Before production
- **Add an authorizer** (JWT / Lambda) to the HTTP API — these endpoints control
  infrastructure and must not be public. (See the architecture audit.)
- Restrict `CorsAllowOrigin` to the exact frontend origin.
