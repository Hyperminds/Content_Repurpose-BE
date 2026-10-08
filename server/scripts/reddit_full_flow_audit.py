"""Trace every step of the Reddit prepare flow against the live page.
Never clicks Post. Reports exactly which step raises.
"""
import argparse
import asyncio

from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.reddit import constants as C
from app.social_publishing.providers.hermes.constants import (
    HERMES_ACTION_TIMEOUT, HERMES_NAVIGATION_TIMEOUT,
)


async def step(label, coro):
    try:
        result = await coro
        print(f"  OK    {label} -> {result!r}")
        return result
    except Exception as e:
        print(f"  FAIL  {label}: {type(e).__name__}: {e}")
        return None


async def run(args):
    adapter = PlaywrightHermesAdapter(f"{args.tenant}_{args.account}")
    await adapter.start()
    try:
        # 1. Auth check
        await step("goto reddit.com", adapter.goto(C.REDDIT_BASE, timeout_s=HERMES_NAVIGATION_TIMEOUT))
        authed = await step("is_visible LOC_USER_MENU", adapter.is_visible(*C.LOC_USER_MENU, timeout_s=HERMES_ACTION_TIMEOUT))
        if not authed:
            print("NOT AUTHENTICATED. Stopping.")
            return

        # 2. Navigate to submit page
        submit_url = C.submit_url(args.subreddit)
        await step(f"goto {submit_url}", adapter.goto(submit_url, timeout_s=HERMES_NAVIGATION_TIMEOUT))
        url = await adapter.current_url()
        print(f"  current_url = {url}")

        # 3. Subreddit check
        if f"/r/{args.subreddit.lower()}/" not in url.lower():
            print(f"  SUBREDDIT MISMATCH: expected /r/{args.subreddit}/ in {url}")
            return
        print(f"  OK    subreddit check passed")

        # 4. _ensure_text_composer path
        text_tab = await step("is_visible LOC_TEXT_TAB (3s)", adapter.is_visible(*C.LOC_TEXT_TAB, timeout_s=C.TAB_PROBE_TIMEOUT))
        if text_tab:
            await step("click LOC_TEXT_TAB", adapter.click(*C.LOC_TEXT_TAB, timeout_s=HERMES_ACTION_TIMEOUT))
        else:
            await step("wait_for LOC_TITLE_INPUT (no tab)", adapter.wait_for(*C.LOC_TITLE_INPUT, timeout_s=HERMES_ACTION_TIMEOUT))

        # 5. Fill title + body
        await step("fill LOC_TITLE_INPUT", adapter.fill(*C.LOC_TITLE_INPUT, "Audit title", timeout_s=HERMES_ACTION_TIMEOUT))
        await step("fill LOC_BODY_INPUT", adapter.fill(*C.LOC_BODY_INPUT, "Audit body", timeout_s=HERMES_ACTION_TIMEOUT))

        # 6. Wait for Post button
        await step("wait_for LOC_POST_BUTTON", adapter.wait_for(*C.LOC_POST_BUTTON, timeout_s=HERMES_ACTION_TIMEOUT))

        print("\n  ALL STEPS PASSED (baseline workflow would reach ACTION_REQUIRED)")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("closed. nothing published.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", default="6a3507ed8472b071889873ba")
    p.add_argument("--account", default="6ab4cde5c36d7cad0abdd02f")
    p.add_argument("--subreddit", default="test")
    p.add_argument("--hold", type=int, default=3)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
