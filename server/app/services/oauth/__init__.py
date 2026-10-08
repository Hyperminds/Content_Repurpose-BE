"""
OAuth lifecycle management.

Provides a unified token store (read, write, refresh, expiry detection) that
any platform publisher can use to retrieve valid access tokens. Platform-specific
OAuth flows (authorize URL generation, code exchange) will live in their own
modules (linkedin_oauth.py, twitter_oauth.py, etc.) in Phase 2.
"""
