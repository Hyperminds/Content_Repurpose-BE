"""READ-ONLY-ish: find which workflow ACTION (fill title / fill body / wait Post)
raises. Fills title+body (does NOT submit, never clicks Post). Reports each step.
"""
import argparse
import asyncio

from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.reddit import constants as C
from app.social_publishing.providers.hermes.constants import HERMES_ACTION_TIMEOUT


async def run(args):
    adapter = PlaywrightHermesAdapter(f"{args.tenant}_{args.account}")
    await adapter.start()
    try:
        await adapter.goto(C.submit_url(args.subreddit), timeout_s=30)
        await asyncio.sleep(3)
        print(f"url = {await adapter.current_url()}")

        async def step(label, coro):
            try:
                await coro
                print(f"  OK    {label}")
            except Exception as e:
                print(f"  FAIL  {label}: {type(e).__name__}: {e}")

        # Mirror the text-post path exactly.
        await step("is_visible Text tab", adapter.is_visible(*C.LOC_TEXT_TAB, timeout_s=5))
        await step("fill Title", adapter.fill(*C.LOC_TITLE_INPUT, "Audit title (not posted)", timeout_s=HERMES_ACTION_TIMEOUT))
        await step("fill Body", adapter.fill(*C.LOC_BODY_INPUT, "Audit body (not posted)", timeout_s=HERMES_ACTION_TIMEOUT))
        await step("wait_for Post button", adapter.wait_for(*C.LOC_POST_BUTTON, timeout_s=HERMES_ACTION_TIMEOUT))

        print(f"\nHolding {args.hold}s. NOT clicking Post.")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("closed. nothing published.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tenant", default="6a3507ed8472b071889873ba")
    p.add_argument("--account", default="6ab4cde5c36d7cad0abdd02f")
    p.add_argument("--subreddit", default="test")
    p.add_argument("--hold", type=int, default=5)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
