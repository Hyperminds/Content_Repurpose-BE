"""
Structured Logging System for TrendZo.
Provides consistent, searchable log output for monitoring and debugging.

Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
All logs include: timestamp, level, module, message, and optional metadata.
"""

import sys
from datetime import datetime, timezone
from app.config import APP_ENV, IS_PRODUCTION


class StructuredLogger:
    """
    Lightweight structured logger.
    In production: JSON format for log aggregation (ELK, Datadog, etc.)
    In development: Human-readable colored output.
    """

    COLORS = {
        "DEBUG":    "\033[90m",   # Gray
        "INFO":     "\033[36m",   # Cyan
        "WARNING":  "\033[33m",   # Yellow
        "ERROR":    "\033[31m",   # Red
        "CRITICAL": "\033[35m",   # Magenta
        "RESET":    "\033[0m",
    }

    def __init__(self, module: str = "app"):
        self.module = module

    def _format(self, level: str, message: str, **meta) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        if IS_PRODUCTION:
            # JSON format for log aggregation
            import json
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "module": self.module,
                "msg": message,
                **meta,
            }
            return json.dumps(entry)
        else:
            # Human-readable colored format
            color = self.COLORS.get(level, "")
            reset = self.COLORS["RESET"]
            prefix = f"{color}[{ts}] [{level:8s}]{reset}"
            meta_str = f" | {meta}" if meta else ""
            return f"{prefix} {message}{meta_str}"

    def debug(self, message: str, **meta):
        if not IS_PRODUCTION:
            print(self._format("DEBUG", message, **meta))

    def info(self, message: str, **meta):
        print(self._format("INFO", message, **meta))

    def warning(self, message: str, **meta):
        print(self._format("WARNING", message, **meta), file=sys.stderr)

    def error(self, message: str, **meta):
        print(self._format("ERROR", message, **meta), file=sys.stderr)

    def critical(self, message: str, **meta):
        print(self._format("CRITICAL", message, **meta), file=sys.stderr)

    # ── Domain-specific log methods ───────────────────────────────────────────

    def ai_call(self, model: str, platform: str, tokens: int = 0, duration_ms: int = 0):
        self.info(f"AI call: {model} → {platform}", tokens=tokens, duration_ms=duration_ms)

    def ai_failure(self, model: str, error: str, platform: str = ""):
        self.error(f"AI failure: {model}", error=error, platform=platform)

    def post_published(self, platform: str, post_id: str, user_id: str):
        self.info(f"Post published: {platform}", post_id=post_id, user_id=user_id)

    def post_failed(self, platform: str, error: str, user_id: str):
        self.warning(f"Post failed: {platform}", error=error, user_id=user_id)

    def auth_event(self, event: str, user_id: str = "", email: str = ""):
        self.info(f"Auth: {event}", user_id=user_id, email=email)

    def ws_event(self, event: str, user_id: str = "", connections: int = 0):
        self.debug(f"WebSocket: {event}", user_id=user_id, connections=connections)


# Global logger instance
log = StructuredLogger("trendzzo")
