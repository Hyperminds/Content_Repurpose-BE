"""
EC2PowerController — first production AWS service.

Implements the AWS `PowerController` interface (startup / shutdown / status)
against a single EC2 instance using boto3. This is the ONLY place in Trendzzo
that touches the AWS SDK; callers depend on the interface + domain models and
catch `app.aws.exceptions`, never boto3.

SOLID / DI:
  • Configuration is injected via `AWSConfigurationProvider` — the class never
    reads `os.environ` and never hardcodes region/instance id.
  • The boto3 Session is injectable (for tests / custom credential setups); the
    EC2 client is built once in a dedicated infra method (`_get_client`), never
    inside the startup/shutdown/status business methods.
  • boto3/botocore are imported lazily so importing this module does not require
    the SDK to be installed until the service is actually used.

Behavior:
  • startup()  → start_instances; if already running, returns success (no-op).
  • shutdown() → stop_instances;  if already stopped, returns success (no-op).
  • status()   → describe_instances → domain InstanceState
                 (STARTING | RUNNING | STOPPING | STOPPED | UNKNOWN).

Resilience: botocore adaptive retries + connect/read timeouts (from the config
provider's RetryPolicy/TimeoutPolicy). Blocking SDK calls run in a threadpool so
the event loop is never blocked. AWS/network errors are translated into the
`app.aws.exceptions` domain hierarchy (throttling, timeout, access denied,
invalid instance id, invalid state).
"""

import asyncio
from typing import Optional

from app.services.logger import StructuredLogger
from app.aws.interfaces.power_controller import PowerController
from app.aws.interfaces.aws_configuration_provider import AWSConfigurationProvider
from app.aws.interfaces.types import InstanceState, InstanceStatus, ShutdownResult, WakeResult
from app.aws.utils.instance_state_mapper import map_instance_state
from app.aws.exceptions import (
    AWSError,
    AWSAccessDeniedError,
    AWSThrottlingError,
    AWSTimeoutError,
    InstanceNotFoundError,
    InvalidInstanceStateError,
    WorkspaceShutdownError,
    WorkspaceWakeError,
)

log = StructuredLogger("aws.ec2")

# Botocore error codes we classify explicitly.
_THROTTLING_CODES = {"Throttling", "ThrottlingException", "RequestLimitExceeded", "RequestThrottled"}
_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
_NOT_FOUND_CODES = {"InvalidInstanceID.NotFound", "InvalidInstanceID.Malformed"}
_INVALID_STATE_CODES = {"IncorrectInstanceState", "InvalidInstanceState"}


class EC2PowerController(PowerController):
    """boto3-backed PowerController for a single EC2 instance."""

    def __init__(
        self,
        config: AWSConfigurationProvider,
        session: Optional["object"] = None,
    ) -> None:
        """
        Args:
            config: resolved AWS configuration (region, instance id, retry +
                timeout policies). Required — injected, never read from env here.
            session: an optional pre-built ``boto3.Session``. If omitted, a
                default ``boto3.Session()`` is created lazily. Injectable for
                tests and custom credential providers.
        """
        self._config = config
        self._session = session
        self._client = None  # built lazily and cached

    # ── infra: client construction (isolated from business logic) ────────────
    def _get_client(self):
        """Build (once) and return the EC2 client. Lazily imports boto3."""
        if self._client is not None:
            return self._client
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:  # pragma: no cover - env-dependent
            raise AWSError("boto3 is required for EC2PowerController (pip install boto3)") from e

        retry = self._config.get_retry_policy()
        timeout = self._config.get_timeout_policy()
        boto_config = Config(
            region_name=self._config.get_region(),
            retries={"max_attempts": retry.max_attempts, "mode": retry.mode},
            connect_timeout=timeout.connect_seconds,
            read_timeout=timeout.read_seconds,
        )
        # A Session with no explicit credentials uses the default provider chain
        # (env / shared config / IAM role) — credentials are never hardcoded.
        session = self._session or boto3.Session()
        self._client = session.client("ec2", config=boto_config)
        return self._client

    def _resolve_instance_id(self, instance_id: Optional[str]) -> str:
        return instance_id or self._config.get_instance_id()

    # ── PowerController API ───────────────────────────────────────────────────
    async def status(self, instance_id: Optional[str] = None) -> InstanceStatus:
        iid = self._resolve_instance_id(instance_id)
        raw = await self._describe_state(iid)
        state = map_instance_state(raw)
        log.info("EC2 status", instance_id=iid, state=state.value, raw_state=raw)
        return InstanceStatus(
            instance_id=iid,
            state=state,
            raw_state=raw,
            detail=f"Instance {iid} is {state.label}",
        )

    async def startup(self, instance_id: Optional[str] = None) -> WakeResult:
        iid = self._resolve_instance_id(instance_id)
        current = map_instance_state(await self._describe_state(iid))

        # Already up (or on the way up) → success, no redundant start call.
        if current in (InstanceState.RUNNING, InstanceState.STARTING):
            log.info("EC2 startup no-op", instance_id=iid, state=current.value)
            return WakeResult(
                instance_id=iid, requested=False, state=current,
                detail=f"Instance already {current.label.lower()}",
            )

        try:
            client = self._get_client()
            resp = await asyncio.to_thread(client.start_instances, InstanceIds=[iid])
            new_raw = resp["StartingInstances"][0]["CurrentState"]["Name"]
        except Exception as e:
            raise self._translate(e, WorkspaceWakeError, "startup", iid) from e

        new_state = map_instance_state(new_raw)
        log.info("EC2 startup requested", instance_id=iid, state=new_state.value, raw_state=new_raw)
        return WakeResult(
            instance_id=iid, requested=True, state=new_state,
            detail=f"Start requested; instance is {new_state.label}",
        )

    async def shutdown(self, instance_id: Optional[str] = None) -> ShutdownResult:
        iid = self._resolve_instance_id(instance_id)
        current = map_instance_state(await self._describe_state(iid))

        # Already down (or on the way down) → success, no redundant stop call.
        if current in (InstanceState.STOPPED, InstanceState.STOPPING):
            log.info("EC2 shutdown no-op", instance_id=iid, state=current.value)
            return ShutdownResult(
                instance_id=iid, requested=False, state=current,
                detail=f"Instance already {current.label.lower()}",
            )

        try:
            client = self._get_client()
            resp = await asyncio.to_thread(client.stop_instances, InstanceIds=[iid])
            new_raw = resp["StoppingInstances"][0]["CurrentState"]["Name"]
        except Exception as e:
            raise self._translate(e, WorkspaceShutdownError, "shutdown", iid) from e

        new_state = map_instance_state(new_raw)
        log.info("EC2 shutdown requested", instance_id=iid, state=new_state.value, raw_state=new_raw)
        return ShutdownResult(
            instance_id=iid, requested=True, state=new_state,
            detail=f"Stop requested; instance is {new_state.label}",
        )

    # ── internal helpers ──────────────────────────────────────────────────────
    async def _describe_state(self, instance_id: str) -> str:
        """Return the raw EC2 instance-state-name (empty string if not found)."""
        try:
            client = self._get_client()
            resp = await asyncio.to_thread(client.describe_instances, InstanceIds=[instance_id])
            reservations = resp.get("Reservations", [])
            instances = reservations[0].get("Instances", []) if reservations else []
            if not instances:
                raise InstanceNotFoundError(f"Instance {instance_id} not found")
            return instances[0]["State"]["Name"]
        except Exception as e:
            raise self._translate(e, AWSError, "status", instance_id) from e

    def _translate(self, exc: Exception, default_cls, action: str, instance_id: str) -> AWSError:
        """
        Translate a boto3/botocore error (or a pass-through domain error) into the
        appropriate `app.aws.exceptions` type. Logs the failure with context.
        """
        # Already a domain error — log and return as-is.
        if isinstance(exc, AWSError):
            log.error("EC2 error", instance_id=instance_id, action=action, error=str(exc), kind=type(exc).__name__)
            return exc

        error_code = None
        try:
            from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError
            if isinstance(exc, ClientError):
                error_code = exc.response.get("Error", {}).get("Code")
            elif isinstance(exc, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError)):
                log.error("EC2 timeout", instance_id=instance_id, action=action, error=str(exc))
                return AWSTimeoutError(f"AWS call timed out during {action}: {exc}")
        except ImportError:
            pass

        if error_code in _THROTTLING_CODES:
            mapped = AWSThrottlingError(f"AWS throttled {action} (retries exhausted)")
        elif error_code in _ACCESS_DENIED_CODES:
            mapped = AWSAccessDeniedError(f"Not authorized to {action} instance {instance_id}")
        elif error_code in _NOT_FOUND_CODES:
            mapped = InstanceNotFoundError(f"Instance {instance_id} not found or invalid")
        elif error_code in _INVALID_STATE_CODES:
            mapped = InvalidInstanceStateError(f"Instance {instance_id} state does not allow {action}")
        else:
            mapped = default_cls(f"AWS error during {action}: {error_code or exc}")

        log.error(
            "EC2 error",
            instance_id=instance_id, action=action,
            error_code=error_code or type(exc).__name__, error=str(exc),
            kind=type(mapped).__name__,
        )
        return mapped
