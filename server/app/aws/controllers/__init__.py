"""
AWS controllers — thin request/orchestration-facing coordinators.

Interfaces-only phase: no implementations yet. Later, controllers here will
adapt the AWS PowerController (and providers) to the application layer — e.g. an
adapter that lets `app.workspace.power` / the Wake Service / Shutdown
Orchestrator drive AWS through the interfaces without importing the AWS module's
concrete classes or boto3.

Controllers depend on the interfaces (constructor injection), keeping transport
and orchestration concerns separate from the SDK-backed services.
"""
