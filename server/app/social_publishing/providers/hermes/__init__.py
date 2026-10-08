"""Hermes external-agent publishing provider.

Hermes executes publishing through a browser agent for platforms where such
automation is permitted. This package contains ONLY the Trendzzo-side
abstraction:

  - HermesPublisherProvider  — satisfies the PublishingProvider interface
  - HermesExecutionAdapter   — the single boundary to the Hermes runtime
  - HermesPlatformWorkflow   — per-platform workflow plugin base
  - FakeHermesWorkflow       — a test/dev workflow (no real browser)
  - FakeHermesExecutionAdapter — simulates all outcomes for tests

NO real browser workflows are implemented here. Building a real platform
workflow requires an explicit, separate compliance review per platform.

Trendzzo remains the control plane; Hermes owns only browser execution.
"""
