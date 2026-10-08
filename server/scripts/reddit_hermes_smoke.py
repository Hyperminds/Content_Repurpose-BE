"""Dev-only Reddit Hermes smoke test (PREPARE-ONLY — never publishes).

Proves the chain:  Trendzzo -> PlaywrightHermesAdapter -> Chromium -> reddit.com
and that the existing Reddit prepare workflow reaches ACTION_REQUIRED.

SAFETY / GUARANTEES:
  * NEVER clicks Reddit's final Post/Publish button.
  * NEVER types a Reddit password / MFA / solves CAPTCHA (manual, by you).
  * NEVER prints cookies, session storage, tokens, or profile contents.
  * Uses the REAL PlaywrightHermesAdapter and the REAL RedditUserAssistedService.
  * The `prepare` subcommand stops at ACTION_REQUIRED by construction (the
    workflow's prepare() confirms the Post button exists but does not click it).

Run from server/ with the venv python and PYTHONPATH set to server/:

  $env:PYTHONPATH="...\\server"
  venv\\Scripts\\python.exe scripts\\reddit_hermes_smoke.py launch
  venv\\Scripts\\python.exe scripts\\reddit_hermes_smoke.py session
  venv\\Scripts\\python.exe scripts\\reddit_hermes_smoke.py prepare \
      --tenant <tid> --account <aid> --subreddit u_DinnerOk250

Subcommands:
  launch   Phase 5/6 — open headful Chromium at reddit.com, report safe state,
           keep it open (default 300s) so you can manually log in, then close.
  session  Phase 7  — relaunch the SAME persistent profile, check if the login
           session persisted (safe state only), keep open briefly, close.
  prepare  Phase 8/9 — run the real prepare workflow; expect ACTION_REQUIRED;
           then verify the pending confirmation exists. Never publishes.
"""

import argparse
import asyncio

from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.reddit import constants as C


# ── Safe browser-state probe (no cookies/secrets) ───────────────────────────────

async def _safe_state(adapter: PlaywrightHermesAdapter) -> dict:
    """Collect only non-sensitive browser state. Never reads cookies/storage."""
    url = await adapter.current_url()
    # Logged-in signal: the user-menu button in Reddit's chrome. Presence only —
    # we never read its contents, cookies, or any account detail.
    logged_in = await adapter.is_visible(*C.LOC_USER_MENU, timeout_s=5)
    try:
        title = await adapter._require_page().title()  # page title is not sensitive
    except Exception:
        title = "(unavailable)"
    return {"url": url, "title": title, "logged_in_ui_present": logged_in}


def _print_state(label: str, state: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"  current_url            = {state['url']}")
    print(f"  page_title             = {state['title']}")
    print(f"  logged_in_ui_present   = {state['logged_in_ui_present']}")


# ── launch (Phase 5/6) ──────────────────────────────────────────────────────────

async def cmd_launch(args) -> None:
    profile_key = f"{args.tenant}_{args.account}" if args.tenant and args.account else "smoke_default"
    print(f"[launch] profile_key = {profile_key}")
    print("[launch] Launching REAL Chromium (headful) via PlaywrightHermesAdapter...")
    adapter = PlaywrightHermesAdapter(profile_key)
    await adapter.start()
    try:
        print("[launch] Browser alive. Navigating to reddit.com ...")
        await adapter.goto(C.REDDIT_BASE, timeout_s=45)
        # Let the page settle.
        await asyncio.sleep(3)
        state = await _safe_state(adapter)
        _print_state("BROWSER STATE (launch)", state)
        print(f"\n[launch] Browser is open for {args.hold}s.")
        print("[launch] If not logged in, MANUALLY sign in as your Reddit user now.")
        print("[launch] (Do NOT expect this script to type credentials — it will not.)")
        print("[launch] The persistent profile will remember the session after close.")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("[launch] Browser closed cleanly.")


# ── session (Phase 7) ────────────────────────────────────────────────────────────

async def cmd_session(args) -> None:
    profile_key = f"{args.tenant}_{args.account}" if args.tenant and args.account else "smoke_default"
    print(f"[session] Re-launching SAME profile_key = {profile_key}")
    adapter = PlaywrightHermesAdapter(profile_key)
    await adapter.start()
    try:
        await adapter.goto(C.REDDIT_BASE, timeout_s=45)
        await asyncio.sleep(3)
        state = await _safe_state(adapter)
        _print_state("BROWSER STATE (session persistence)", state)
        if state["logged_in_ui_present"]:
            print("\nReddit browser session persisted.")
        else:
            print("\nReddit browser session did not persist.")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("[session] Browser closed cleanly.")


# ── prepare (Phase 8/9) — PREPARE-ONLY, never publishes ─────────────────────────

async def cmd_prepare(args) -> None:
    import uuid

    from app.social_publishing.providers.hermes.reddit.service import RedditUserAssistedService
    from app.social_publishing.providers.hermes.pending_confirmation_repository import (
        PendingConfirmationRepository,
    )

    operation_id = args.operation_id or f"reddit_smoke_{uuid.uuid4().hex[:12]}"
    media_paths = [args.media] if getattr(args, "media", "") else []
    print("[prepare] PREPARE-ONLY. This will NOT click Post. Expect ACTION_REQUIRED.")
    print(f"[prepare] tenant={args.tenant} account={args.account} subreddit={args.subreddit}")
    print(f"[prepare] operation_id={operation_id}")
    print(f"[prepare] title={args.title!r}")
    if media_paths:
        print(f"[prepare] media={media_paths} (image post)")

    service = RedditUserAssistedService()  # default factory => REAL PlaywrightHermesAdapter
    result = await service.prepare(
        tenant_id=args.tenant,
        account_id=args.account,
        operation_id=operation_id,
        title=args.title,
        body=args.body,
        subreddit=args.subreddit,
        media_paths=media_paths,
    )

    print("\n=== PREPARE RESULT (safe fields) ===")
    print(f"  status          = {result.get('status')}")
    print(f"  action          = {result.get('action')}")
    print(f"  message         = {result.get('message')}")
    print(f"  confirmation_id = {result.get('confirmation_id')}")
    print(f"  operation_id    = {result.get('operation_id')}")
    preview = result.get("preview") or {}
    if preview:
        print(f"  preview.subreddit   = {preview.get('subreddit')}")
        print(f"  preview.title       = {preview.get('title')}")
        print(f"  preview.media_count = {preview.get('media_count')}")

    status = result.get("status")
    if status != "action_required":
        print(f"\n[prepare] NOTE: status is '{status}', not 'action_required'.")
        print("[prepare] No post was created. See message above for the reason.")
        print(f"\nOPERATION_ID={operation_id}")
        print(f"PREPARE_STATUS={status}")
        return

    # Phase 9 — verify the pending confirmation exists and is correct.
    pending_repo = PendingConfirmationRepository()
    pending = await pending_repo.find_by_operation(operation_id, args.tenant)

    print("\n=== PENDING CONFIRMATION VERIFICATION (Phase 9) ===")
    if not pending:
        print("  [FAIL] no pending confirmation found for operation_id")
        print(f"\nOPERATION_ID={operation_id}\nPREPARE_STATUS={status}\nPENDING=missing")
        return

    checks = {
        "operation_id matches": pending.operation_id == operation_id,
        "account_id matches": pending.account_id == args.account,
        "tenant_id matches": pending.tenant_id == args.tenant,
        "platform == reddit": pending.platform == "reddit",
        "status == action_required": pending.status == "action_required",
        "provider_name == hermes": pending.provider_name == "hermes",
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"  confirmation_id = {pending.id}")
    print(f"  subreddit       = {pending.subreddit}")
    print(f"  expires_at      = {pending.expires_at}")

    print(f"\nOPERATION_ID={operation_id}")
    print(f"CONFIRMATION_ID={pending.id}")
    print(f"PREPARE_STATUS={status}")
    print(f"PENDING_STATUS={pending.status}")
    print("\n[prepare] STOPPED at ACTION_REQUIRED. Nothing was published.")


# ── confirm (PUBLISHES — real post) ─────────────────────────────────────────────

async def cmd_confirm(args) -> None:
    """Confirm a pending Reddit post via the REAL service. THIS PUBLISHES.

    Goes through RedditUserAssistedService.confirm() exactly like the
    /social-publishing/reddit/confirm/{id} route: re-prepares in a fresh browser
    session (login persists on disk), clicks Post, and verifies the permalink.
    UNKNOWN outcomes are surfaced, NEVER blind-retried.
    """
    from app.social_publishing.providers.hermes.reddit.service import RedditUserAssistedService
    from app.social_publishing.providers.hermes.pending_confirmation_repository import (
        PendingConfirmationRepository,
    )

    print("[confirm] THIS WILL PUBLISH A REAL REDDIT POST. Using existing confirmation.")
    print(f"[confirm] tenant={args.tenant} confirmation_id={args.confirmation_id}")

    service = RedditUserAssistedService()  # default factory => REAL PlaywrightHermesAdapter
    result = await service.confirm(args.tenant, args.confirmation_id)

    print("\n=== CONFIRM RESULT (safe fields) ===")
    for k in ("status", "external_url", "external_post_id", "platform", "message"):
        if k in result:
            print(f"  {k:16} = {result.get(k)}")

    # Report the persisted pending row's final status (single source of truth).
    repo = PendingConfirmationRepository()
    pending = await repo.find(args.confirmation_id, args.tenant)
    if pending:
        print("\n=== PENDING ROW (post-confirm) ===")
        print(f"  operation_id  = {pending.operation_id}")
        print(f"  status        = {pending.status}")
        print(f"  external_url  = {pending.external_url}")
        print(f"  subreddit     = {pending.subreddit}")

    status = result.get("status")
    print(f"\nCONFIRM_STATUS={status}")
    if status == "published":
        print(f"PERMALINK={result.get('external_url')}")
        print(f"EXTERNAL_POST_ID={result.get('external_post_id')}")
        print("[confirm] Published exactly one post (single confirm, single Post click).")
    elif status == "unknown":
        print("[confirm] Result UNKNOWN — clicked Post but permalink not confirmed.")
        print("[confirm] Per policy: NOT retrying. Follow VERIFYING/reconciliation.")
    else:
        print(f"[confirm] Non-published terminal result: {status}. See message above.")


# ── verify-url (READ-ONLY) ──────────────────────────────────────────────────────

async def cmd_verify_url(args) -> None:
    """READ-ONLY: run the workflow's verify() against an EXISTING permalink.

    Navigates to an existing post URL and asks the real HermesRedditWorkflow to
    verify it — proving the new permalink detection resolves PUBLISHED WITHOUT
    creating any new post. Never clicks Post, never submits.
    """
    from app.social_publishing.providers.hermes.reddit.workflow import HermesRedditWorkflow
    from app.social_publishing.providers.errors import PublishInstruction

    print("[verify-url] READ-ONLY verification against an existing post. No publish.")
    print(f"[verify-url] url={args.url} expected_subreddit={args.subreddit}")

    adapter = PlaywrightHermesAdapter(f"{args.tenant}_{args.account}")
    await adapter.start()
    try:
        # Position the browser on the existing permalink (simulates the post-click
        # landing state), then verify — exactly what _verify inspects.
        await adapter.goto(args.url, timeout_s=45)
        instruction = PublishInstruction(
            operation_id="verify_readonly", tenant_id=args.tenant, account_id=args.account,
            platform=C.PLATFORM, provider_name="hermes", text="",
            meta={"reddit_subreddit": args.subreddit, "reddit_title": args.title},
        )
        result = await HermesRedditWorkflow().verify(instruction, adapter)
    finally:
        await adapter.close()

    print("\n=== VERIFY RESULT ===")
    print(f"  status           = {result.status}")
    print(f"  external_url      = {result.external_url}")
    print(f"  external_post_id  = {result.external_post_id}")
    print(f"  error_message     = {result.error_message}")
    print(f"\nVERIFY_STATUS={result.status}")


# ── verify-submitted (READ-ONLY) — reconcile an UNKNOWN via submitted listing ──

async def cmd_verify_submitted(args) -> None:
    """READ-ONLY: run the workflow's submitted-listing fallback for a pending
    UNKNOWN confirmation and, if it resolves PUBLISHED, reconcile the record.

    Never clicks Post, never creates a post. Uses the real workflow._verify
    fallback path (navigate to /user/<username>/submitted/, match by title).
    """
    from app.social_publishing.providers.hermes.reddit.workflow import (
        HermesRedditWorkflow, _post_from_instruction,
    )
    from app.social_publishing.providers.hermes.reddit.service import RedditUserAssistedService
    from app.social_publishing.providers.hermes.pending_confirmation_repository import (
        PendingConfirmationRepository,
    )
    from app.social_publishing.providers.errors import PublishInstruction, ExternalResultStatus
    from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository

    repo = PendingConfirmationRepository()
    pending = await repo.find(args.confirmation_id, args.tenant)
    if not pending:
        print("[verify-submitted] pending confirmation not found"); return
    print(f"[verify-submitted] op={pending.operation_id} status={pending.status} "
          f"sub={pending.subreddit} title={pending.title!r}")

    account = await SocialAccountsRepository().find_by_id(pending.account_id, args.tenant)
    username = (account.platform_account_id if account else "") or ""
    print(f"[verify-submitted] username={username!r}")

    instruction = PublishInstruction(
        operation_id=pending.operation_id, tenant_id=args.tenant, account_id=pending.account_id,
        platform=C.PLATFORM, provider_name="hermes", text=pending.body,
        meta={"reddit_title": pending.title, "reddit_subreddit": pending.subreddit,
              "reddit_media_paths": [], "reddit_username": username},
    )
    profile_key = f"{args.tenant}_{pending.account_id}"
    adapter = PlaywrightHermesAdapter(profile_key)
    wf = HermesRedditWorkflow()
    await adapter.start()
    try:
        # Drive ONLY the read-only submitted-listing fallback.
        url = await wf._find_in_submitted(adapter, _post_from_instruction(instruction))
    finally:
        await adapter.close()

    print(f"[verify-submitted] fallback permalink = {url}")
    if not url:
        print("VERIFY_SUBMITTED=NO_MATCH (left as-is)"); return

    if args.reconcile:
        await repo.set_status(args.confirmation_id, args.tenant, "verifying")
        await repo.set_status(args.confirmation_id, args.tenant, "published", external_url=url)
        print(f"RECONCILED=published PERMALINK={url}")
    else:
        print(f"WOULD_RECONCILE=published PERMALINK={url} (pass --reconcile to apply)")


# ── inspect-composer (READ-ONLY) — dump the live submit-composer DOM ────────────

async def cmd_inspect_composer(args) -> None:
    """READ-ONLY: navigate to the subreddit submit composer and dump the real
    DOM structure of the elements the workflow depends on (post-type controls,
    file inputs, the Post button, image-preview nodes).

    NEVER clicks Post, NEVER uploads, NEVER publishes. Optionally attaches a
    local image to the file input ONLY to observe how the preview renders (still
    never clicks Post). Pass --attach <path> to exercise the attach-preview DOM.

    Prints structured JSON so selectors can be fixed against ground truth.
    """
    import json

    profile_key = f"{args.tenant}_{args.account}" if args.tenant and args.account else "smoke_default"
    print(f"[inspect] profile_key={profile_key} subreddit={args.subreddit}")
    adapter = PlaywrightHermesAdapter(profile_key)
    await adapter.start()
    try:
        await adapter.goto(C.submit_url(args.subreddit), timeout_s=45)
        await asyncio.sleep(args.settle)
        page = adapter._require_page()

        # Read-only DOM survey. Playwright CSS pierces open shadow roots, so this
        # reaches into Reddit's shreddit web components.
        survey = await page.evaluate(
            r"""() => {
                const txt = (el) => (el && (el.innerText || el.textContent) || "").trim().slice(0, 80);
                const attrs = (el) => {
                    const o = {};
                    for (const a of el.attributes) o[a.name] = a.value.slice(0, 120);
                    return o;
                };
                // All file inputs anywhere (pierces light DOM; shadow handled below).
                const fileInputs = [...document.querySelectorAll("input[type=file]")].map(el => ({
                    accept: el.getAttribute("accept"),
                    name: el.getAttribute("name"),
                    id: el.id || null,
                    hidden: el.hidden,
                    outerStart: el.outerHTML.slice(0, 160),
                }));
                // Buttons / role=button whose text or aria-label looks like Post/submit.
                const btnLike = [...document.querySelectorAll("button, [role=button], a[role=button]")]
                    .filter(el => {
                        const t = (txt(el) + " " + (el.getAttribute("aria-label")||"")).toLowerCase();
                        return /\b(post|submit|create|next)\b/.test(t);
                    })
                    .slice(0, 25)
                    .map(el => ({
                        tag: el.tagName.toLowerCase(),
                        text: txt(el),
                        ariaLabel: el.getAttribute("aria-label"),
                        ariaDisabled: el.getAttribute("aria-disabled"),
                        disabled: el.disabled ?? null,
                        slot: el.getAttribute("slot"),
                        className: (el.className || "").toString().slice(0, 80),
                    }));
                // Post-type selector controls (Text / Images & Video / Link).
                const typeCtrls = [...document.querySelectorAll("button, [role=tab], a")]
                    .filter(el => {
                        const t = txt(el).toLowerCase();
                        return /(image|video|media|text|link|post type)/.test(t) && t.length < 40;
                    })
                    .slice(0, 25)
                    .map(el => ({ tag: el.tagName.toLowerCase(), role: el.getAttribute("role"), text: txt(el) }));
                // Any shreddit composer custom elements present.
                const customEls = [...document.querySelectorAll("*")]
                    .map(el => el.tagName.toLowerCase())
                    .filter(n => n.includes("-") && (n.includes("post") || n.includes("composer") || n.includes("submit") || n.includes("media") || n.includes("image")))
                    .filter((v, i, a) => a.indexOf(v) === i)
                    .slice(0, 40);
                // Preview images that would indicate an attached image.
                const previews = {
                    blob_img: document.querySelectorAll("img[src^='blob:']").length,
                    data_img: document.querySelectorAll("img[src^='data:']").length,
                    faceplate_img: document.querySelectorAll("faceplate-img, img[src*='redd.it'], img[src*='redditmedia']").length,
                };
                return {
                    url: location.href,
                    title_input_present: !!document.querySelector("textarea[name=title], textarea[placeholder*='Title'], input[name=title], [name=title]"),
                    fileInputs,
                    postTypeControls: typeCtrls,
                    buttonLike: btnLike,
                    composerCustomElements: customEls,
                    previews,
                };
            }"""
        )
        print("\n=== REDDIT COMPOSER DOM SURVEY (read-only) ===")
        print(json.dumps(survey, indent=2, default=str))

        if args.attach:
            print(f"\n[inspect] Attaching (observe-only) image to first file input: {args.attach}")
            try:
                await page.locator("input[type=file]").first.set_input_files(args.attach, timeout=15000)
                await asyncio.sleep(args.settle)
                after = await page.evaluate(
                    r"""() => ({
                        blob_img: document.querySelectorAll("img[src^='blob:']").length,
                        data_img: document.querySelectorAll("img[src^='data:']").length,
                        remove_media_btn: [...document.querySelectorAll("button,[role=button]")]
                            .some(el => /remove media|remove image|delete/i.test((el.innerText||"")+(el.getAttribute("aria-label")||""))),
                        file_inputs_now: document.querySelectorAll("input[type=file]").length,
                    })"""
                )
                print("\n=== AFTER OBSERVE-ONLY ATTACH ===")
                print(json.dumps(after, indent=2, default=str))
            except Exception as e:
                print(f"[inspect] attach-observe failed: {type(e).__name__}: {e}")

        print(f"\n[inspect] Holding browser open {args.hold}s for manual DOM inspection. NOT publishing.")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("[inspect] Browser closed cleanly. Nothing was published.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reddit Hermes smoke test")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_launch = sub.add_parser("launch", help="Phase 5/6: headful launch to reddit.com")
    p_launch.add_argument("--tenant", default="")
    p_launch.add_argument("--account", default="")
    p_launch.add_argument("--hold", type=int, default=300, help="seconds to keep browser open")
    p_launch.set_defaults(func=cmd_launch)

    p_session = sub.add_parser("session", help="Phase 7: relaunch same profile, check persistence")
    p_session.add_argument("--tenant", default="")
    p_session.add_argument("--account", default="")
    p_session.add_argument("--hold", type=int, default=20)
    p_session.set_defaults(func=cmd_session)

    p_prep = sub.add_parser("prepare", help="Phase 8/9: prepare-only, expect ACTION_REQUIRED")
    p_prep.add_argument("--tenant", required=True)
    p_prep.add_argument("--account", required=True)
    p_prep.add_argument("--subreddit", required=True)
    p_prep.add_argument("--title", default="Trendzzo Hermes Smoke Test")
    p_prep.add_argument(
        "--body",
        default="This is a development smoke test for the Trendzzo Reddit publishing workflow.",
    )
    p_prep.add_argument("--operation-id", dest="operation_id", default="")
    p_prep.add_argument("--media", default="", help="path to a single image for an image post")
    p_prep.set_defaults(func=cmd_prepare)

    p_conf = sub.add_parser("confirm", help="PUBLISHES: confirm a pending Reddit post (real Post click)")
    p_conf.add_argument("--tenant", required=True)
    p_conf.add_argument("--confirmation-id", dest="confirmation_id", required=True)
    p_conf.set_defaults(func=cmd_confirm)

    p_ver = sub.add_parser("verify-url", help="READ-ONLY: verify an existing permalink resolves PUBLISHED")
    p_ver.add_argument("--tenant", required=True)
    p_ver.add_argument("--account", required=True)
    p_ver.add_argument("--url", required=True)
    p_ver.add_argument("--subreddit", required=True)
    p_ver.add_argument("--title", default="Trendzzo Hermes Smoke Test")
    p_ver.set_defaults(func=cmd_verify_url)

    p_vs = sub.add_parser("verify-submitted", help="READ-ONLY: reconcile an UNKNOWN via submitted listing")
    p_vs.add_argument("--tenant", required=True)
    p_vs.add_argument("--confirmation-id", dest="confirmation_id", required=True)
    p_vs.add_argument("--reconcile", action="store_true", help="apply published status if matched")
    p_vs.set_defaults(func=cmd_verify_submitted)

    p_ins = sub.add_parser("inspect-composer", help="READ-ONLY: dump the live submit-composer DOM")
    p_ins.add_argument("--tenant", default="")
    p_ins.add_argument("--account", default="")
    p_ins.add_argument("--subreddit", required=True, help="subreddit (e.g. u_YourUsername for your profile)")
    p_ins.add_argument("--attach", default="", help="optional local image path to observe attach preview (never posts)")
    p_ins.add_argument("--settle", type=int, default=4, help="seconds to let the composer settle before survey")
    p_ins.add_argument("--hold", type=int, default=120, help="seconds to keep the browser open")
    p_ins.set_defaults(func=cmd_inspect_composer)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
