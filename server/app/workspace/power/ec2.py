"""
EC2PowerController — real AWS EC2 sleep/wake strategy (boto3).

Implements the PowerController contract against a single EC2 instance:
    startup()  → ec2.start_instances     (bring the workspace up)
    shutdown() → ec2.stop_instances      (complete stop, so the instance halts)
    status()   → ec2.describe_instances  → Running | Stopped | Stopping | Pending

Configuration comes exclusively from the environment:
    AWS_REGION   — the instance's region (required)
    INSTANCE_ID  — the target instance id, e.g. i-0abc123… (required)
    AWS_MAX_ATTEMPTS — optional, botocore retry attempts (default 5)

Credentials are NEVER hardcoded. boto3 resolves them via the default provider
chain (environment variables, shared config/credentials files, or — in
production — the EC2 instance's IAM role). The IAM principal needs
ec2:StopInstances, ec2:StartInstances and ec2:DescribeInstances.

Design notes:
  • boto3/botocore are imported LAZILY so the app runs in "local" mode without
    the dependency installed. Only selecting the "ec2" strategy requires boto3.
  • The blocking boto3 calls run in a threadpool (asyncio.to_thread) so they
    never block the event loop.
  • Transient failures are retried by botocore (adaptive mode). Any remaining
    AWS/network error is caught and returned as a structured error result
    (ok=False) — methods never raise into the orchestrator.
"""

import asyncio
import os
from typing import Optional

from app.services.logger import log
from app.workspace.power.base import PowerController, PowerState

# EC2 instance-state-name → public label (per the required contract).
_STATE_LABEL = {
    "pending": "Pending",
    "running": "Running",
    "stopping": "Stopping",
    "stopped": "Stopped",
    "shutting-down": "Stopping",
    "terminated": "Terminated",
}

# EC2 instance-state-name → our coarse PowerState.
_STATE_ENUM = {
    "pending": PowerState.STARTING,
    "running": PowerState.RUNNING,
    "stopping": PowerState.STOPPING,
    "stopped": PowerState.SLEEPING,
    "shutting-down": PowerState.STOPPING,
    "terminated": PowerState.UNKNOWN,
}


class EC2PowerController(PowerController):
    """Stops/starts a specific EC2 instance to sleep/wake the workspace."""

    name = "ec2"

    def __init__(
        self,
        region: Optional[str] = None,
        instance_id: Optional[str] = None,
        max_attempts: Optional[int] = None,
    ) -> None:
        self.region = region or os.getenv("AWS_REGION")
        self.instance_id = instance_id or os.getenv("INSTANCE_ID")
        self._max_attempts = max_attempts or int(os.getenv("AWS_MAX_ATTEMPTS", "5"))
        self._client = None  # created lazily on first use

        # Fail fast on misconfiguration — this strategy is useless without both.
        if not self.region:
            raise ValueError("EC2PowerController requires the AWS_REGION environment variable")
        if not self.instance_id:
            raise ValueError("EC2PowerController requires the INSTANCE_ID environment variable")

    # ── boto3 client (lazy) ──────────────────────────────────────────────────
    def _get_client(self):
        """Create (once) and return a configured EC2 client. Lazy-imports boto3."""
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "boto3 is required for EC2PowerController. Install it with `pip install boto3`."
            ) from e

        cfg = Config(
            region_name=self.region,
            # Adaptive retries transparently handle throttling + transient errors.
            retries={"max_attempts": self._max_attempts, "mode": "adaptive"},
            connect_timeout=5,
            read_timeout=15,
        )
        # No credentials passed here — resolved via the default provider chain
        # (env / shared config / IAM instance role). Never hardcode secrets.
        self._client = boto3.client("ec2", config=cfg)
        return self._client

    # ── actions ──────────────────────────────────────────────────────────────
    async def shutdown(self) -> dict:
        """Complete stop of the EC2 instance (ec2:StopInstances)."""
        return await self._stop("shutdown")

    async def startup(self) -> dict:
        """Start the EC2 instance (ec2:StartInstances)."""
        return await self._start("startup")

    async def _stop(self, action: str) -> dict:
        try:
            client = self._get_client()
            resp = await asyncio.to_thread(client.stop_instances, InstanceIds=[self.instance_id])
            state = resp["StoppingInstances"][0]["CurrentState"]["Name"]
            log.info(f"[power:ec2] stop_instances → {self.instance_id} now '{state}' ({action})")
            return self._ec2_result(action, state)
        except Exception as e:
            return self._error_result(action, e)

    async def _start(self, action: str) -> dict:
        try:
            client = self._get_client()
            resp = await asyncio.to_thread(client.start_instances, InstanceIds=[self.instance_id])
            state = resp["StartingInstances"][0]["CurrentState"]["Name"]
            log.info(f"[power:ec2] start_instances → {self.instance_id} now '{state}' ({action})")
            return self._ec2_result(action, state)
        except Exception as e:
            return self._error_result(action, e)

    async def status(self) -> dict:
        """Describe the instance and report Running / Stopped / Stopping / Pending."""
        try:
            client = self._get_client()
            resp = await asyncio.to_thread(
                client.describe_instances, InstanceIds=[self.instance_id]
            )
            reservations = resp.get("Reservations", [])
            instances = reservations[0].get("Instances", []) if reservations else []
            if not instances:
                res = self._result(
                    "status", ok=False, state=PowerState.UNKNOWN,
                    detail=f"Instance {self.instance_id} not found",
                )
                res["instance_id"] = self.instance_id
                res["instance_state"] = "Unknown"
                return res
            state = instances[0]["State"]["Name"]
            return self._ec2_result("status", state)
        except Exception as e:
            return self._error_result("status", e)

    # ── result builders ──────────────────────────────────────────────────────
    def _ec2_result(self, action: str, ec2_state: str) -> dict:
        """Wrap an EC2 state name into the uniform result + contract label."""
        label = _STATE_LABEL.get(ec2_state, ec2_state.title() if ec2_state else "Unknown")
        res = self._result(
            action,
            ok=True,
            state=_STATE_ENUM.get(ec2_state, PowerState.UNKNOWN),
            detail=f"EC2 instance {self.instance_id} is {label}",
        )
        res["instance_id"] = self.instance_id
        res["instance_state"] = label  # one of: Running | Stopped | Stopping | Pending | …
        return res

    def _error_result(self, action: str, exc: Exception) -> dict:
        """Turn an AWS/network error into a structured, non-raising result."""
        error_code = None
        try:
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_code = exc.response.get("Error", {}).get("Code")
        except Exception:
            pass

        # Classify the failure so callers/ops can react appropriately. botocore's
        # adaptive retry mode already retries throttling + transient errors before
        # this point; anything surfacing here is a final failure.
        THROTTLING = {"Throttling", "ThrottlingException", "RequestLimitExceeded", "RequestThrottled"}
        INVALID_STATE = {"IncorrectInstanceState", "InvalidInstanceID.NotFound", "InvalidInstanceID.Malformed"}

        if error_code in THROTTLING:
            category = "throttling"
            detail = "AWS is throttling requests (retries exhausted). Try again shortly."
        elif error_code in INVALID_STATE:
            category = "invalid_instance_state"
            detail = (
                f"Instance {self.instance_id} is in a state that doesn't allow '{action}' "
                f"(e.g. still stopping/pending, or the id is invalid)."
            )
        elif error_code:
            category = "aws_error"
            detail = f"AWS error during {action}: {error_code}"
        else:
            # No response object → network failure / timeout / aborted request.
            category = "network"
            detail = f"Network error during {action}: {exc}"

        log.error(f"[power:ec2] {action}() failed [{category}/{error_code or type(exc).__name__}]: {exc}")
        res = self._result(action, ok=False, state=PowerState.UNKNOWN, detail=detail)
        res["instance_id"] = self.instance_id
        res["category"] = category
        res["error"] = str(exc)
        if error_code:
            res["error_code"] = error_code
        return res
