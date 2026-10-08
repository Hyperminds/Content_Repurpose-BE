"""AI Social Publishing Agent.

The agent is a decision-making layer that generates structured publishing plans.
It NEVER directly calls platform APIs. It produces validated data structures
that the deterministic publishing engine executes after human approval.

Architecture:
    AI Agent → PublishingPlan → Human Approval → Publishing Engine → Platform Adapter

Safety constraints:
    - Never requests social passwords
    - Never bypasses OAuth/CAPTCHA/MFA
    - Never imitates human browser behavior
    - Never publishes without explicit permission
    - All output is validated before execution
"""
