"""
app.core — shared infrastructure / cross-cutting concerns.

Houses framework-level wiring that is reused across feature services but is NOT
business logic: the shared AI client, identity helpers, and (re-exported)
configuration. Feature services import from here instead of re-implementing the
same setup.
"""
