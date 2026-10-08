"""BYOK (Bring Your Own API Credentials) provider abstraction (Phase 20).

This package defines HOW Trendzzo will support customers supplying their own
legitimate platform API credentials (e.g. their own X API app), WITHOUT
implementing every concrete BYOK flow yet.

Design summary
--------------
    User
      |
      v
    Connect "X API" (BYOK)
      |
      v
    User's own developer credentials (client id/secret or API key)
      |
      v
    Encrypted credential storage (CredentialVault, tenant+account scoped)
      |
      v
    BYOK X Provider  (a PublishingProvider with provider_type = BYOK_API)

Rules
-----
  - A BYOK provider is ALWAYS tenant + account scoped. It resolves the caller's
    own credentials from encrypted storage for that specific account only.
  - Credentials are encrypted at rest (Fernet via CredentialVault) exactly like
    OAuth tokens. They are NEVER returned to the frontend after storage and
    NEVER logged.
  - A BYOK provider uses the platform's OFFICIAL API — it is not a bypass for
    pricing, rate limits, or platform restrictions.
  - Routing: ProviderRouter.resolve() dispatches BYOK accounts by provider_name
    (e.g. "byok_x"), so multiple BYOK providers can coexist.

Only the abstraction + a credential-store contract are provided here. Concrete
per-platform BYOK providers (e.g. a real BYOK X publisher) are future work,
gated behind ENABLE_BYOK_PROVIDERS.
"""
