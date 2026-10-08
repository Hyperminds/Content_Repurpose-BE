"""READ-ONLY: probe which Reddit composer elements are visible/missing.

Opens the persistent-profile browser at r/<subreddit>/submit and checks
every selector the Reddit workflow depends on. Reports found/missing for
each. Never clicks Post, never publishes, never modifies any workflow code.

Usage (from server/):
  $env:PYTHONPATH="."
  ..\venv\Scripts\python.exe scripts\reddit_element_audit.py --subreddit test
"""

import argparse
import asyncio

from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.reddit import constants as C
from app.social_publishing.providers.hermes.semantic_browser import BrowserTimeout


async def audit(args):
    profile_key = f"{args.tenant}_{args.account}" if args.tenant and args.account else "smoke_default"
    adapter = PlaywrightHermesAdapter(profile_key)
    await adapter.start()
    try:
        # 1. Check auth
        print("[audit] Navigating to reddit.com ...")
        await adapter.goto(C.REDDIT_BASE, timeout_s=30)
        await asyncio.sleep(2)
        logged_in = await adapter.is_visible(*C.LOC_USER_MENU, timeout_s=5)
        print(f"  LOC_USER_MENU {C.LOC_USER_MENU}: {'FOUND' if logged_in else 'MISSING'}")
        if not logged_in:
            print("[audit] NOT LOGGED IN. Cannot proceed. Run the smoke launch command and sign in first.")
            return

        # 2. Navigate to submit page
        submit = C.submit_url(args.subreddit)
        print(f"[audit] Navigating to {submit} ...")
        await adapter.goto(submit, timeout_s=30)
        await asyncio.sleep(3)
        url = await adapter.current_url()
        print(f"  current_url = {url}")

        # 3. Probe every element the workflow uses (5s timeout each)
        probes = [
            ("LOC_TEXT_TAB", C.LOC_TEXT_TAB),
            ("LOC_IMAGE_BUTTON", C.LOC_IMAGE_BUTTON),
            ("LOC_IMAGE_TAB", C.LOC_IMAGE_TAB),
            ("LOC_TITLE_INPUT", C.LOC_TITLE_INPUT),
            ("LOC_BODY_INPUT", C.LOC_BODY_INPUT),
            ("LOC_POST_BUTTON", C.LOC_POST_BUTTON),
        ]
        print("\n[audit] Probing workflow elements (5s timeout each):")
        for label, (role, name) in probes:
            found = await adapter.is_visible(role, name, timeout_s=5)
            status = "FOUND" if found else "MISSING"
            print(f"  {status:7}  {label:20} = ({role!r}, {name!r})")

        # 4. CSS probes for common Reddit composer elements
        css_probes = [
            ("file input", "input[type=file]"),
            ("title textarea", "textarea[name='title']"),
            ("title input", "input[name='title']"),
            ("shreddit-composer", "shreddit-composer"),
            ("shreddit-post-composer", "shreddit-post-composer"),
            ("faceplate-text-input", "faceplate-text-input"),
            ("[slot=title]", "[slot='title']"),
            ("[placeholder*=Title]", "[placeholder*='Title']"),
            ("[aria-label*=title]", "[aria-label*='title']"),
            ("[aria-label*=Title]", "[aria-label*='Title']"),
            ("button with Post text", "button"),
        ]
        print("\n[audit] CSS probes (3s timeout each):")
        for label, sel in css_probes:
            found = await adapter.is_css_visible(sel, timeout_s=3)
            status = "FOUND" if found else "MISSING"
            print(f"  {status:7}  {label:30} = {sel}")

        # 5. Full page JS survey of relevant elements
        print("\n[audit] JS DOM survey:")
        survey = await adapter._require_page().evaluate(r"""() => {
            const results = {};
            // All textareas
            results.textareas = [...document.querySelectorAll('textarea')].map(el => ({
                name: el.name, placeholder: (el.placeholder||'').slice(0,60),
                ariaLabel: el.getAttribute('aria-label'),
                id: el.id || null, visible: el.offsetParent !== null
            }));
            // All inputs (text-like)
            results.textInputs = [...document.querySelectorAll('input[type=text], input:not([type])')].map(el => ({
                name: el.name, placeholder: (el.placeholder||'').slice(0,60),
                ariaLabel: el.getAttribute('aria-label'),
                id: el.id || null, visible: el.offsetParent !== null
            }));
            // All buttons with relevant text
            results.buttons = [...document.querySelectorAll('button, [role=button]')]
                .filter(el => {
                    const t = ((el.innerText||'')+(el.getAttribute('aria-label')||'')).toLowerCase();
                    return /post|submit|title|text|image|video|link|next|create/.test(t);
                })
                .slice(0, 30)
                .map(el => ({
                    tag: el.tagName.toLowerCase(), text: (el.innerText||'').trim().slice(0,40),
                    ariaLabel: el.getAttribute('aria-label'), role: el.getAttribute('role'),
                    disabled: el.disabled ?? null, ariaDisabled: el.getAttribute('aria-disabled'),
                    visible: el.offsetParent !== null
                }));
            // Custom elements with post/compose/submit in the name
            results.customElements = [...new Set(
                [...document.querySelectorAll('*')]
                    .map(el => el.tagName.toLowerCase())
                    .filter(n => n.includes('-') && (n.includes('post') || n.includes('compos') || n.includes('submit') || n.includes('title') || n.includes('editor')))
            )].slice(0, 30);
            // contenteditable elements
            results.contentEditable = [...document.querySelectorAll('[contenteditable=true]')].map(el => ({
                tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
                ariaLabel: el.getAttribute('aria-label'),
                placeholder: el.getAttribute('placeholder') || el.getAttribute('aria-placeholder') || null,
                text: (el.innerText||'').slice(0,40), visible: el.offsetParent !== null
            }));
            return results;
        }""")
        import json
        print(json.dumps(survey, indent=2, default=str))

        print(f"\n[audit] Done. Browser staying open {args.hold}s for manual inspection.")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("[audit] Browser closed. Nothing was published.")


def main():
    p = argparse.ArgumentParser(description="Reddit element audit (read-only)")
    p.add_argument("--tenant", default="6a3507ed8472b071889873ba")
    p.add_argument("--account", default="6ab4cde5c36d7cad0abdd02f")
    p.add_argument("--subreddit", default="test")
    p.add_argument("--hold", type=int, default=30, help="seconds to keep browser open")
    args = p.parse_args()
    asyncio.run(audit(args))


if __name__ == "__main__":
    main()
