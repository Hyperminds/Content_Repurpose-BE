"""Hybrid publishing provider layer.

This package introduces the provider abstraction that sits ABOVE the existing
platform publishers. It lets Trendzzo route a publishing job to different
*execution methods* (native API, external browser agent, user-assisted agent,
BYOK) without the scheduler or worker knowing how a post is published.

Trendzzo remains the control plane (users, tenants, accounts, content,
scheduling, jobs, retries, idempotency, state, audit). Providers own ONLY
platform execution.

Nothing here replaces the existing `publishers/` package, `PublisherRegistry`,
OAuth infrastructure, scheduler, worker, or native integrations.
"""
