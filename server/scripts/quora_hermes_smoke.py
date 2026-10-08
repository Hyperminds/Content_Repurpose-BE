"""Dev-only Quora Hermes smoke test (prepare + confirm, separate commands).

Proves the chain:  Trendzzo -> PlaywrightHermesAdapter -> Chromium -> quora.com
via the REAL QuoraUserAssistedService and HermesQuoraWorkflow.

SAFETY / GUARANTEES:
  * `prepare` stops at ACTION_REQUIRED by construction — it NEVER clicks Post.
  * `confirm` is a SEPARATE command — it is NOT called automatically after
    prepare, so the first real test can be inspected manually before publishing.
  * NEVER types a password / MFA / solves CAPTCHA (manual, by you).
  * NEVER prints cookies, session storage, tokens, or profile contents.

Run from server/ with the venv python and PYTHONPATH set to server/:

  $env:PYTHONPATH="...\\server"
  venv\\Scripts\\python.exe scripts\\quora_hermes_smoke.py prepare \
      --tenant <t> --account <a> --text "..." [--media <path>]
  venv\\Scripts\\python.exe scripts\\quora_hermes_smoke.py confirm \
      --tenant <t> --confirmation-id <id>

Subcommands:
  prepare  Prepare-only; expect ACTION_REQUIRED; prints the confirmation_id.
           Never clicks Post.
  confirm  Confirm + publish via the production service. THIS PUBLISHES.
"""

import argparse
import asyncio
import uuid


async def cmd_prepare(args):
    from app.social_publishing.providers.hermes.quora.service import QuoraUserAssistedService
    from app.social_publishing.providers.hermes.pending_confirmation_repository import (
        PendingConfirmationRepository,
    )

    operation_id = args.operation_id or f"quora_smoke_{uuid.uuid4().hex[:12]}"
    media = [args.media] if args.media else []
    print("[prepare] PREPARE-ONLY. This will NOT click Post. Expect ACTION_REQUIRED.")
    print(f"[prepare] tenant={args.tenant} account={args.account}")
    print(f"[prepare] operation_id={operation_id} media={media} text={args.text!r}")

    service = QuoraUserAssistedService()  # default factory => REAL adapter
    result = await service.prepare(
        tenant_id=args.tenant, account_id=args.account, operation_id=operation_id,
        text=args.text, media_paths=media,
    )
    print("\n=== PREPARE RESULT (safe fields) ===")
    for k in ("status", "action", "message", "confirmation_id", "operation_id"):
        print(f"  {k:16} = {result.get(k)}")
    preview = result.get("preview") or {}
    if preview:
        print(f"  preview.text        = {preview.get('text')}")
        print(f"  preview.media_count = {preview.get('media_count')}")

    status = result.get("status")
    if status != "action_required":
        print(f"\n[prepare] status is '{status}', not 'action_required'. Nothing published.")
        print(f"OPERATION_ID={operation_id}\nPREPARE_STATUS={status}")
        return

    pending = await PendingConfirmationRepository().find_by_operation(operation_id, args.tenant)
    print("\n=== PENDING CONFIRMATION VERIFICATION ===")
    if not pending:
        print("  [FAIL] no pending confirmation found")
    else:
        checks = {
            "operation_id matches": pending.operation_id == operation_id,
            "account_id matches": pending.account_id == args.account,
            "tenant_id matches": pending.tenant_id == args.tenant,
            "platform == quora": pending.platform == "quora",
            "status == action_required": pending.status == "action_required",
            "provider_name == hermes": pending.provider_name == "hermes",
        }
        for label, ok in checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"  confirmation_id = {pending.id}")
    print(f"\nOPERATION_ID={operation_id}\nPREPARE_STATUS={status}")
    print("[prepare] STOPPED at ACTION_REQUIRED. Nothing was published.")
    print("[prepare] To publish, run the 'confirm' subcommand with the confirmation_id above.")


async def cmd_confirm(args):
    """Confirm + publish a previously-prepared Quora post via the production service."""
    from app.social_publishing.providers.hermes.quora.service import QuoraUserAssistedService
    from app.social_publishing.providers.hermes.pending_confirmation_repository import (
        PendingConfirmationRepository,
    )

    tenant = args.tenant
    confirmation_id = args.confirmation_id
    print("[confirm] Confirm + publish via production QuoraUserAssistedService.confirm().")
    print(f"[confirm] tenant={tenant} confirmation_id={confirmation_id}")

    repo = PendingConfirmationRepository()
    pending = await repo.find(confirmation_id, tenant)
    operation_id = getattr(pending, "operation_id", None) if pending else None
    account_id = getattr(pending, "account_id", None) if pending else None
    if pending is None:
        print("[confirm] NOTE: no pending confirmation found (service will report 'not_found').")

    service = QuoraUserAssistedService()  # default factory => REAL adapter
    result = await service.confirm(tenant_id=tenant, confirmation_id=confirmation_id)

    status = result.get("status")
    permalink = result.get("external_url")
    external_post_id = result.get("external_post_id")
    reason = result.get("message") or result.get("error") or result.get("reason")

    print("\n=== CONFIRM RESULT (safe fields) ===")
    print(f"  status            = {status}")
    print(f"  operation_id      = {operation_id}")
    print(f"  platform          = {result.get('platform') or 'quora'}")
    print(f"  provider          = hermes")
    if account_id:
        print(f"  account_id        = {account_id}")
    if permalink:
        print(f"  permalink         = {permalink}")
    if external_post_id:
        print(f"  external_post_id  = {external_post_id}")
    if reason:
        print(f"  reason            = {reason}")

    print("\n=== PUBLISH VERDICT ===")
    if status == "published":
        print("  [SUCCESS] Quora post PUBLISHED.")
        if permalink:
            print(f"            permalink: {permalink}")
    elif status == "unknown":
        print("  [UNKNOWN] Post was submitted but the result is uncertain — the")
        print("            post MAY exist. Verify manually; do NOT blind-retry.")
    elif status == "not_found":
        print("  [NOT PUBLISHED] No pending confirmation matched this id/tenant.")
    else:
        print(f"  [NOT PUBLISHED] status={status}. Nothing was published.")
    print(f"\nCONFIRM_STATUS={status}")


def main():
    p = argparse.ArgumentParser(description="Quora Hermes smoke test (prepare + confirm)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("prepare")
    pp.add_argument("--tenant", required=True)
    pp.add_argument("--account", required=True)
    pp.add_argument("--text", default="Trendzzo Hermes Quora smoke test")
    pp.add_argument("--media", default="")
    pp.add_argument("--operation-id", dest="operation_id", default="")
    pp.set_defaults(func=cmd_prepare)

    pc = sub.add_parser("confirm")
    pc.add_argument("--tenant", required=True)
    pc.add_argument("--confirmation-id", dest="confirmation_id", required=True)
    pc.set_defaults(func=cmd_confirm)

    args = p.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
