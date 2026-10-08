"""Reddit user-assisted Hermes workflow.

The FIRST real Hermes platform workflow. Reddit publishing is USER-ASSISTED:
Hermes (via the semantic browser boundary) prepares the post and then STOPS —
the user must explicitly confirm before the final submit. No silent/autonomous
posting, no MFA/CAPTCHA bypass, no anti-bot evasion.

Contents:
  - constants.py   — URLs, limits, supported media
  - validation.py  — subreddit normalization + content validation
  - workflow.py     — HermesRedditWorkflow (implements HermesPlatformWorkflow)
"""
