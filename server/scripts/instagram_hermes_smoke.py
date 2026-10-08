"""Dev-only Instagram Hermes smoke test (PREPARE-ONLY — never publishes).

Proves the chain:  Trendzzo -> PlaywrightHermesAdapter -> Chromium -> instagram.com
and that the Instagram prepare workflow reaches ACTION_REQUIRED.

SAFETY / GUARANTEES:
  * NEVER clicks Instagram's Share button.
  * NEVER types a password / MFA / solves CAPTCHA (manual, by you).
  * NEVER prints cookies, session storage, tokens, or profile contents.
  * Uses the REAL PlaywrightHermesAdapter and REAL InstagramUserAssistedService.
  * `prepare` stops at ACTION_REQUIRED by construction (the workflow confirms the
    Share control exists but does not click it).

Run from server/ with the venv python and PYTHONPATH set to server/:

  $env:PYTHONPATH="...\\server"
  venv\\Scripts\\python.exe scripts\\instagram_hermes_smoke.py launch  --tenant <t> --account <a>
  venv\\Scripts\\python.exe scripts\\instagram_hermes_smoke.py session --tenant <t> --account <a>
  venv\\Scripts\\python.exe scripts\\instagram_hermes_smoke.py probe   --tenant <t> --account <a>
  venv\\Scripts\\python.exe scripts\\instagram_hermes_smoke.py prepare --tenant <t> --account <a> --media <path> --caption "..."

Subcommands:
  launch   Open headful Chromium at instagram.com, report safe state, keep open
           so you can log in manually, then close.
  session  Relaunch the SAME persistent profile, check if the login persisted.
  probe    READ-ONLY: after login, inspect the create-post composer's roles/names
           so the constants' best-effort locators can be corrected. Does NOT
           attach media or click Share.
  prepare  Prepare-only; expect ACTION_REQUIRED; verify pending confirmation.
           Never clicks Share.
"""

import argparse
import asyncio

from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.instagram import constants as C


def _profile_key(tenant, account):
    # Must match InstagramUserAssistedService._profile_key (ig_ namespaced).
    return f"ig_{tenant}_{account}" if tenant and account else "ig_smoke_default"


async def _safe_state(adapter):
    url = await adapter.current_url()
    try:
        title = await adapter._require_page().title()
    except Exception:
        title = "(unavailable)"
    # Auth signal: a Create/New-post control present (presence only).
    logged_in = False
    for role, name in C.LOC_AUTH_SIGNAL_CANDIDATES:
        try:
            if await adapter.is_visible(role, name, timeout_s=4):
                logged_in = True
                break
        except Exception:
            pass
    return {"url": url, "title": title, "logged_in_ui_present": logged_in}


def _print_state(label, s):
    print(f"\n=== {label} ===")
    print(f"  current_url          = {s['url']}")
    print(f"  page_title           = {s['title']}")
    print(f"  logged_in_ui_present = {s['logged_in_ui_present']}")


async def cmd_launch(args):
    pk = _profile_key(args.tenant, args.account)
    print(f"[launch] profile_key = {pk}")
    print("[launch] Launching REAL Chromium (headful) via PlaywrightHermesAdapter...")
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)
        _print_state("BROWSER STATE (launch)", await _safe_state(adapter))
        print(f"\n[launch] Browser open for {args.hold}s. Log into Instagram MANUALLY if needed.")
        print("[launch] (This script will NOT type credentials or solve MFA/CAPTCHA.)")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("[launch] Browser closed cleanly.")


async def cmd_session(args):
    pk = _profile_key(args.tenant, args.account)
    print(f"[session] Re-launching SAME profile_key = {pk}")
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)
        s = await _safe_state(adapter)
        _print_state("BROWSER STATE (session persistence)", s)
        print("\nInstagram browser session persisted." if s["logged_in_ui_present"]
              else "\nInstagram browser session did not persist.")
        await asyncio.sleep(args.hold)
    finally:
        await adapter.close()
        print("[session] Browser closed cleanly.")


async def cmd_probe(args):
    """READ-ONLY composer inspection to correct locators. No media, no Share."""
    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)
        print(f"[probe] home url={page.url} title={await page.title()!r}")

        async def names(role, limit=60):
            """Accessible names for a role — includes role='button' AND
            div[role=button] etc. (Instagram uses div-role buttons heavily)."""
            loc = page.get_by_role(role)
            n = await loc.count()
            out = []
            for i in range(min(n, limit)):
                try:
                    el = loc.nth(i)
                    if not await el.is_visible():
                        continue
                    nm = await el.get_attribute("aria-label") or (await el.inner_text())[:40]
                    nm = (nm or "").replace("\n", " ").strip()
                    if nm:
                        out.append(nm)
                except Exception:
                    pass
            # De-dup, preserve order.
            seen, uniq = set(), []
            for x in out:
                if x not in seen:
                    seen.add(x); uniq.append(x)
            return uniq

        async def file_input_report(tag):
            # Count file inputs by DOM presence (NOT visibility — they're hidden),
            # plus their accept attribute. This is the reliable attach target.
            fi = page.locator("input[type=file]")
            n = await fi.count()
            accepts = []
            for i in range(min(n, 8)):
                try:
                    accepts.append(await fi.nth(i).get_attribute("accept"))
                except Exception:
                    pass
            print(f"  [{tag}] input[type=file] DOM count={n} accepts={accepts}")

        async def dump(step):
            print(f"\n=== STEP: {step} ===")
            print("  links:", await names("link"))
            print("  buttons:", await names("button"))
            print("  textboxes:", await names("textbox"))
            print("  dialogs:", await names("dialog"))
            await file_input_report(step)
            # contenteditable (the caption editor is often a contenteditable div).
            try:
                ce = page.locator("[contenteditable='true']")
                print(f"  contenteditable count={await ce.count()}")
            except Exception:
                pass

        async def dialog_report(tag):
            try:
                d = page.locator(C.COMPOSER_DIALOG_SELECTOR)
                print(f"  [{tag}] role=dialog count={await d.count()}")
            except Exception:
                pass

        await dump("HOME (before opening composer)")

        # STEP 1 — click the Create control (opens the flyout on the current UI).
        opened = None
        for role, name in C.LOC_CREATE_CANDIDATES:
            try:
                if await adapter.is_visible(role, name, timeout_s=3):
                    await adapter.click(role, name, timeout_s=8)
                    opened = (role, name); break
            except Exception:
                pass
        print(f"\n[probe] clicked Create via = {opened}")
        await asyncio.sleep(2)
        await dump("AFTER Create click (flyout expected)")
        await dialog_report("AFTER Create click")

        # STEP 2 — click the 'Post' flyout item that opens the composer modal.
        posted = None
        for role, name in C.LOC_CREATE_POST_SUBMENU_CANDIDATES:
            try:
                if await adapter.is_visible(role, name, timeout_s=3):
                    await adapter.click(role, name, timeout_s=8)
                    posted = (role, name); break
            except Exception:
                pass
        print(f"\n[probe] clicked Post-submenu via = {posted}")
        await asyncio.sleep(3)
        await dump("AFTER Post submenu (composer modal expected)")
        await dialog_report("AFTER Post submenu")

        # Explicit composer-open verdict using the workflow's own signal.
        from app.social_publishing.providers.hermes.instagram.workflow import HermesInstagramWorkflow
        wf = HermesInstagramWorkflow()
        composer_open = await wf._composer_open(adapter)
        print(f"\n[probe] COMPOSER OPEN (dialog or file input mounted) = {composer_open}")

        # STEP 3 — 'Select from computer' usually mounts/reveals the file input.
        # We DO NOT attach a file (read-only). We only click to reveal the input
        # then re-report the DOM file-input count.
        selected = None
        for role, name in C.LOC_SELECT_FROM_COMPUTER_CANDIDATES:
            try:
                if await adapter.is_visible(role, name, timeout_s=2):
                    # NOTE: clicking this may open the OS file picker. We avoid
                    # that by NOT clicking; instead we just report whether the
                    # control exists. (Clicking is left to the real workflow via
                    # the hidden input.)
                    selected = (role, name); break
            except Exception:
                pass
        print(f"\n[probe] 'Select from computer' control present = {selected}")
        await file_input_report("composer (final)")

        print("\n[probe] NOTE: no media attached, Share NOT clicked. Paste the")
        print("        full STEP dumps so locators in")
        print("        providers/hermes/instagram/constants.py can be corrected.")
    finally:
        await adapter.close()
        print("[probe] closed cleanly.")


async def cmd_probe_post(args):
    """READ-ONLY DEEP inspector for the Create-flyout 'Post' item.

    Opens the Create flyout (that single click is required to REVEAL the menu —
    it is NOT a Post candidate), then enumerates EVERY element matching 'Post'
    across four strategies and dumps full attributes + ancestor chain + whether
    each sits inside the Create flyout/menu. It CLICKS NONE of the candidates,
    attaches no media, and never clicks Share.
    """
    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        # Reveal the Create flyout (required to render the 'Post' menu item).
        # This clicks the Create control ONLY — never a Post candidate.
        opened_via = None
        for role, name in C.LOC_CREATE_CANDIDATES:
            try:
                if await adapter.is_visible(role, name, timeout_s=3):
                    await adapter.click(role, name, timeout_s=8)
                    opened_via = (role, name)
                    break
            except Exception:
                pass
        print(f"[probe-post] opened Create flyout via = {opened_via}")
        await asyncio.sleep(2)

        # JS run on each element handle: gathers all requested attributes, the
        # ancestor chain (2-3 levels), and a heuristic for "inside a menu/flyout"
        # (nearest ancestor with role=menu/dialog/listbox or aria-hidden state).
        DETAIL_JS = r"""
        (el) => {
          const rect = el.getBoundingClientRect();
          const cs = window.getComputedStyle(el);
          const anc = [];
          let p = el.parentElement, depth = 0, menuAncestor = null;
          while (p && depth < 6) {
            const r = p.getAttribute && p.getAttribute('role');
            if (!menuAncestor && r && ['menu','dialog','listbox','navigation'].includes(r)) {
              menuAncestor = r + (p.getAttribute('aria-label') ? (':'+p.getAttribute('aria-label')) : '');
            }
            if (depth < 3) {
              anc.push({
                tag: p.tagName ? p.tagName.toLowerCase() : '',
                role: p.getAttribute ? p.getAttribute('role') : null,
                cls: (p.className && p.className.toString ? p.className.toString() : '').slice(0, 80),
                testid: p.getAttribute ? p.getAttribute('data-testid') : null,
              });
            }
            p = p.parentElement; depth++;
          }
          let oh = el.outerHTML || '';
          if (oh.length > 300) oh = oh.slice(0, 300) + '…';
          return {
            tag: el.tagName ? el.tagName.toLowerCase() : '',
            innerText: (el.innerText || '').trim().slice(0, 60),
            role: el.getAttribute('role'),
            ariaLabel: el.getAttribute('aria-label'),
            href: el.getAttribute('href'),
            title: el.getAttribute('title'),
            testid: el.getAttribute('data-testid'),
            cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 100),
            box: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
            display: cs.display, visibility: cs.visibility,
            menuAncestor: menuAncestor,
            ancestors: anc,
            outerHTML: oh,
          };
        }
        """

        async def dump_all(loc, label):
            try:
                cnt = await loc.count()
            except Exception as e:
                print(f"\n### {label}: count error {type(e).__name__}")
                return
            print(f"\n### {label}: count={cnt}")
            for i in range(cnt):
                el = loc.nth(i)
                try:
                    visible = await el.is_visible()
                    enabled = await el.is_enabled()
                    acc = None
                    try:
                        acc = await el.evaluate("(e) => e.getAttribute('aria-label') || (e.innerText||'').trim()")
                    except Exception:
                        pass
                    d = await el.evaluate(DETAIL_JS)
                except Exception as ex:
                    print(f"  [{i}] inspect error {type(ex).__name__}")
                    continue
                print(f"  [{i}] tag={d['tag']} role={d['role']!r} visible={visible} enabled={enabled}")
                print(f"       innerText={d['innerText']!r} accName={acc!r} ariaLabel={d['ariaLabel']!r}")
                print(f"       href={d['href']!r} title={d['title']!r} data-testid={d['testid']!r}")
                print(f"       class={d['cls']!r}")
                print(f"       box={d['box']} display={d['display']} visibility={d['visibility']}")
                print(f"       insideMenu/flyout={d['menuAncestor']!r}")
                anc_str = " > ".join(
                    f"{a['tag']}[role={a['role']},testid={a['testid']}]" for a in d["ancestors"]
                )
                print(f"       ancestors(2-3)= {anc_str}")
                print(f"       outerHTML={d['outerHTML']!r}")

        print("\n================ ALL 'Post' CANDIDATES (READ-ONLY, NO CLICKS) ================")
        await dump_all(page.get_by_role("link", name="Post"), "1) get_by_role('link', name='Post')")
        await dump_all(page.get_by_text("Post", exact=True), "2) get_by_text('Post', exact=True)")
        # 3) anchor elements whose visible text contains 'Post'
        await dump_all(page.locator("a", has_text="Post"), "3) a:has-text('Post')")
        # 4) any element whose EXACT visible text is 'Post' (leaf-level)
        await dump_all(page.locator("xpath=//*[normalize-space(text())='Post']"),
                       "4) //*[normalize-space(text())='Post']")

        # Also locate the Create control itself, to describe DOM relationship.
        print("\n================ CREATE CONTROL (for DOM relationship) ================")
        await dump_all(page.get_by_role("link", name="Create"), "Create (role=link)")

        print("\n[probe-post] READ-ONLY complete. No candidate was clicked, no media")
        print("             attached, Share NOT clicked. Paste the full output so the")
        print("             exact Create→Post element can be identified.")
    finally:
        await adapter.close()
        print("[probe-post] closed cleanly.")


async def cmd_probe_create(args):
    """READ-ONLY (except clicking the Create control): prove whether clicking
    Create actually opens the Instagram Create flyout.

    1) Enumerate all 'Create' candidates with full detail.
    2) Snapshot DOM state BEFORE.
    3) Click ONLY the Create candidate the current probe would click.
    4) Wait, re-snapshot, and diff (URL, overlays, menus, new Post/Story/Reel/Live).
    5) Search 'Post' ONLY inside newly-visible overlay/menu containers.
    6) Screenshot AFTER.
    Never clicks Post/Story/Reel/Live, never attaches media, never Shares.
    """
    import os

    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        DETAIL_JS = r"""
        (el) => {
          const rect = el.getBoundingClientRect();
          let oh = el.outerHTML || ''; if (oh.length > 260) oh = oh.slice(0,260)+'…';
          return {
            tag: el.tagName ? el.tagName.toLowerCase() : '',
            role: el.getAttribute('role'),
            ariaLabel: el.getAttribute('aria-label'),
            innerText: (el.innerText||'').trim().slice(0,60),
            href: el.getAttribute('href'),
            title: el.getAttribute('title'),
            testid: el.getAttribute('data-testid'),
            cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0,90),
            box: {x:Math.round(rect.x),y:Math.round(rect.y),w:Math.round(rect.width),h:Math.round(rect.height)},
            outerHTML: oh,
          };
        }"""

        async def dump_all(loc, label):
            try:
                cnt = await loc.count()
            except Exception as e:
                print(f"\n### {label}: count error {type(e).__name__}"); return
            print(f"\n### {label}: count={cnt}")
            for i in range(cnt):
                el = loc.nth(i)
                try:
                    vis = await el.is_visible(); en = await el.is_enabled()
                    acc = await el.evaluate("(e)=> e.getAttribute('aria-label') || (e.innerText||'').trim()")
                    d = await el.evaluate(DETAIL_JS)
                except Exception as ex:
                    print(f"  [{i}] inspect error {type(ex).__name__}"); continue
                print(f"  [{i}] tag={d['tag']} role={d['role']!r} visible={vis} enabled={en}")
                print(f"       innerText={d['innerText']!r} accName={acc!r} ariaLabel={d['ariaLabel']!r}")
                print(f"       href={d['href']!r} title={d['title']!r} data-testid={d['testid']!r} class={d['cls']!r}")
                print(f"       box={d['box']}")
                print(f"       outerHTML={d['outerHTML']!r}")

        # ── STEP 1: enumerate Create candidates ──────────────────────────────
        print("================ CREATE CANDIDATES (before click) ================")
        await dump_all(page.get_by_role("link", name="Create"), "get_by_role('link','Create')")
        await dump_all(page.get_by_role("button", name="Create"), "get_by_role('button','Create')")
        await dump_all(page.get_by_text("Create", exact=True), "get_by_text('Create', exact=True)")

        # ── STEP 2: snapshot BEFORE ──────────────────────────────────────────
        async def snapshot(tag):
            js = r"""
            () => {
              const q = (sel) => document.querySelectorAll(sel).length;
              const vis = (sel) => Array.from(document.querySelectorAll(sel))
                 .filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; }).length;
              return {
                url: location.href,
                dialogs: q("[role=dialog]"), dialogsVis: vis("[role=dialog]"),
                menus: q("[role=menu]"), menusVis: vis("[role=menu]"),
                menuitems: q("[role=menuitem]"),
                listboxes: q("[role=listbox]"),
                fileInputs: q("input[type=file]"),
                bodyLen: document.body.innerHTML.length,
              };
            }"""
            s = await page.evaluate(js)
            print(f"  [{tag}] url={s['url']}")
            print(f"  [{tag}] dialogs={s['dialogs']}(vis {s['dialogsVis']}) menus={s['menus']}(vis {s['menusVis']}) "
                  f"menuitems={s['menuitems']} listboxes={s['listboxes']} fileInputs={s['fileInputs']} bodyLen={s['bodyLen']}")
            return s

        print("\n================ DOM SNAPSHOT: BEFORE ================")
        before = await snapshot("before")

        # ── STEP 3: click ONLY the Create candidate the probe would click ────
        target = None
        for role, name in C.LOC_CREATE_CANDIDATES:
            try:
                if await adapter.is_visible(role, name, timeout_s=3):
                    target = (role, name); break
            except Exception:
                pass
        print(f"\n[probe-create] the probe would click Create via = {target}")
        if target is None:
            print("[probe-create] No visible Create candidate — cannot proceed."); return
        # Report the exact element that this (role,name) resolves to first.
        try:
            first = (await adapter._resolve_visible(*target, timeout_s=6))
            if first is not None:
                d = await first.evaluate(DETAIL_JS)
                print(f"[probe-create] resolved Create element: tag={d['tag']} role={d['role']!r} "
                      f"href={d['href']!r} box={d['box']} outerHTML={d['outerHTML']!r}")
        except Exception as ex:
            print(f"[probe-create] resolve-describe error {type(ex).__name__}")

        await adapter.click(*target, timeout_s=8)  # the ONLY click in this probe
        await asyncio.sleep(3)

        # ── STEP 4/5: snapshot AFTER + diff ──────────────────────────────────
        print("\n================ DOM SNAPSHOT: AFTER Create click ================")
        after = await snapshot("after")

        print("\n================ BEFORE → AFTER DIFF ================")
        print(f"  url changed:      {before['url']!r} -> {after['url']!r}  ({before['url'] != after['url']})")
        print(f"  visible dialogs:  {before['dialogsVis']} -> {after['dialogsVis']}")
        print(f"  visible menus:    {before['menusVis']} -> {after['menusVis']}")
        print(f"  menuitems:        {before['menuitems']} -> {after['menuitems']}")
        print(f"  listboxes:        {before['listboxes']} -> {after['listboxes']}")
        print(f"  file inputs:      {before['fileInputs']} -> {after['fileInputs']}")
        print(f"  bodyLen delta:    {after['bodyLen'] - before['bodyLen']}")

        # ── STEP 6: search Post/Story/Reel/Live ONLY inside overlay/menu nodes ─
        overlay_scan_js = r"""
        () => {
          const words = ['Post','Story','Reel','Live'];
          // Overlay/menu containers only — NOT the whole page.
          const containers = Array.from(document.querySelectorAll(
            "[role=dialog],[role=menu],[role=listbox],[aria-modal=true]"
          ));
          const out = [];
          for (const c of containers) {
            const items = c.querySelectorAll("a,button,[role=menuitem],[role=button],div[tabindex],span");
            for (const it of items) {
              const t = (it.innerText||'').trim();
              if (!t) continue;
              if (words.some(w => t === w || t.startsWith(w))) {
                const r = it.getBoundingClientRect();
                if (r.width<=0 || r.height<=0) continue;
                out.push({
                  text: t.slice(0,30),
                  tag: it.tagName.toLowerCase(),
                  role: it.getAttribute('role'),
                  href: it.getAttribute('href'),
                  testid: it.getAttribute('data-testid'),
                  containerRole: c.getAttribute('role') || (c.getAttribute('aria-modal') ? 'aria-modal' : ''),
                  box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
                });
              }
            }
          }
          return out;
        }"""
        overlay_hits = await page.evaluate(overlay_scan_js)
        print("\n================ Post/Story/Reel/Live INSIDE overlays/menus ONLY ================")
        if not overlay_hits:
            print("  (none found inside any dialog/menu/listbox/aria-modal container)")
        for h in overlay_hits:
            print(f"  text={h['text']!r} tag={h['tag']} role={h['role']!r} containerRole={h['containerRole']!r} "
                  f"href={h['href']!r} data-testid={h['testid']!r} box={h['box']}")

        # ── STEP 7: verdict ──────────────────────────────────────────────────
        opened_overlay = (after['dialogsVis'] > before['dialogsVis']
                          or after['menusVis'] > before['menusVis']
                          or after['menuitems'] > before['menuitems']
                          or after['listboxes'] > before['listboxes'])
        print("\n================ VERDICT ================")
        print(f"  Create click caused URL change:      {before['url'] != after['url']}")
        print(f"  Create click opened a NEW overlay/menu: {opened_overlay}")
        print(f"  Post/Story/Reel/Live visible in overlay: {len(overlay_hits) > 0}")

        # ── STEP 8: screenshot AFTER ─────────────────────────────────────────
        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")),
            "_diag")
        os.makedirs(shot_dir, exist_ok=True)
        shot = os.path.join(shot_dir, "instagram_after_create_click.png")
        try:
            await adapter.screenshot(shot)
            print(f"\n[probe-create] screenshot saved: {shot}")
        except Exception as ex:
            print(f"\n[probe-create] screenshot failed: {type(ex).__name__}")

        print("\n[probe-create] READ-ONLY complete. Only the Create control was clicked;")
        print("               no Post/Story/Reel/Live clicked, no media, no Share.")
    finally:
        await adapter.close()
        print("[probe-create] closed cleanly.")


async def cmd_probe_create_candidates(args):
    """FULLY READ-ONLY: discover the real Instagram sidebar publishing/Create
    control from the live DOM. Clicks NOTHING. Makes no assumptions about the
    accessible name being 'Create'.

    Enumerates candidate nav controls in the left sidebar / navigation region,
    dumps full detail for each, and gives special attention to the element at
    ~x=12,y=412,w=48,h=56 (what the current resolver wrongly clicks), including
    its full ancestry and descendants. Saves a homepage screenshot first.
    """
    import os

    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        # ── STEP 11: screenshot the authenticated homepage BEFORE anything ───
        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")),
            "_diag")
        os.makedirs(shot_dir, exist_ok=True)
        shot = os.path.join(shot_dir, "instagram_home_before.png")
        try:
            await adapter.screenshot(shot)
            print(f"[probe-cc] homepage screenshot saved: {shot}")
        except Exception as ex:
            print(f"[probe-cc] screenshot failed: {type(ex).__name__}")

        vw = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
        print(f"[probe-cc] viewport = {vw}")

        # One big read-only JS sweep: collect actionable elements, classify by
        # position (left sidebar = small x, tall column) and by publish-y hints.
        SWEEP_JS = r"""
        () => {
          const VW = window.innerWidth, VH = window.innerHeight;
          const HINT = /create|new post|compose|add|post|reel|story|share/i;
          const inLeftSidebar = (r) => r.width>0 && r.height>0 && r.left < Math.min(260, VW*0.22) && r.height <= VH;
          // Candidate actionable elements.
          const sel = "a, button, [role=link], [role=button], [role=menuitem], div[tabindex], svg[aria-label], [aria-label]";
          const nodes = Array.from(document.querySelectorAll(sel));
          const seen = new Set();
          const out = [];
          for (const el of nodes) {
            const r = el.getBoundingClientRect();
            const visible = r.width>0 && r.height>0
              && window.getComputedStyle(el).visibility !== 'hidden'
              && window.getComputedStyle(el).display !== 'none';
            if (!visible) continue;
            const aria = el.getAttribute('aria-label') || '';
            const title = el.getAttribute('title') || '';
            const text = (el.innerText||'').trim();
            const href = el.getAttribute('href');
            const left = inLeftSidebar(r);
            const hint = HINT.test(aria) || HINT.test(title) || HINT.test(text);
            // Keep it focused: left-sidebar items, OR anything with a publish-y
            // aria-label/title anywhere, OR href="#" actionable icons.
            if (!(left || hint || href === '#')) continue;
            // De-dup by tag+box.
            const key = el.tagName + Math.round(r.x) + 'x' + Math.round(r.y) + '_' + Math.round(r.width);
            if (seen.has(key)) continue; seen.add(key);
            // aria-label of a child svg (icon-only buttons label the svg).
            let svgLabel = null;
            const svg = el.querySelector && el.querySelector('svg[aria-label]');
            if (svg) svgLabel = svg.getAttribute('aria-label');
            // classify
            let cls = 'unknown';
            if (left && (href === '#' || el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' || el.getAttribute('role') === 'link')) cls = 'sidebar navigation candidate';
            else if (href && /^\/[^#]/.test(href)) cls = 'profile/content candidate';
            let oh = el.outerHTML || ''; if (oh.length > 220) oh = oh.slice(0,220)+'…';
            out.push({
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role'),
              text: text.slice(0,40),
              aria: aria || null,
              svgLabel: svgLabel,
              title: title || null,
              href: href,
              testid: el.getAttribute('data-testid'),
              cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0,80),
              box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
              inLeftSidebar: left,
              publishHint: hint,
              classification: cls,
              outerHTML: oh,
            });
          }
          // Sort by vertical position within the sidebar for readability.
          out.sort((a,b) => (a.box.x - b.box.x) || (a.box.y - b.box.y));
          return out;
        }"""
        cands = await page.evaluate(SWEEP_JS)
        print(f"\n================ SIDEBAR / PUBLISH-HINT CANDIDATES ({len(cands)}) — READ ONLY ================")
        for i, c in enumerate(cands):
            print(f"\n[{i}] {c['classification']}  box={c['box']}  inLeftSidebar={c['inLeftSidebar']} publishHint={c['publishHint']}")
            print(f"     tag={c['tag']} role={c['role']!r} text={c['text']!r} aria={c['aria']!r} svgLabel={c['svgLabel']!r}")
            print(f"     title={c['title']!r} href={c['href']!r} data-testid={c['testid']!r} class={c['cls']!r}")
            print(f"     outerHTML={c['outerHTML']!r}")

        # ── STEP 7/8: deep-dive the element at ~x=12,y=412,w=48,h=56 ─────────
        # Locate by geometry (center point), then dump ancestry + descendants.
        FOCUS_JS = r"""
        () => {
          // Center of the reported box ~ (12+24, 412+28) = (36, 440). Use
          // elementsFromPoint to find the stack there.
          const pts = [[36,440],[24,436],[36,440]];
          let target = null;
          for (const [x,y] of pts) {
            const stack = document.elementsFromPoint(x,y);
            // pick the nearest anchor/button/role element in the stack
            target = stack.find(e => ['A','BUTTON'].includes(e.tagName) || ['link','button'].includes(e.getAttribute('role'))) || stack[0];
            if (target) break;
          }
          if (!target) return null;
          const desc = (el) => {
            const r = el.getBoundingClientRect();
            let oh = el.outerHTML || ''; if (oh.length>200) oh = oh.slice(0,200)+'…';
            return {
              tag: el.tagName.toLowerCase(), role: el.getAttribute('role'),
              aria: el.getAttribute('aria-label'), title: el.getAttribute('title'),
              href: el.getAttribute('href'), testid: el.getAttribute('data-testid'),
              text: (el.innerText||'').trim().slice(0,40),
              box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
              outerHTML: oh,
            };
          };
          // ancestry up to 6 levels
          const anc = []; let p = target, d = 0;
          while (p && d < 7) { anc.push(desc(p)); p = p.parentElement; d++; }
          // descendants with useful attrs
          const kids = Array.from(target.querySelectorAll('svg[aria-label], [aria-label], a, button')).slice(0,10).map(desc);
          // immediate siblings of the target's parent (the nav column items)
          const sibs = [];
          if (target.parentElement && target.parentElement.parentElement) {
            const col = target.parentElement.parentElement;
            for (const s of Array.from(col.children).slice(0,15)) sibs.push(desc(s));
          }
          return { target: desc(target), ancestry: anc, descendants: kids, siblingColumn: sibs };
        }"""
        focus = await page.evaluate(FOCUS_JS)
        print("\n================ FOCUS: element the current resolver CLICKS (~x=12,y=412) ================")
        if not focus:
            print("  (no element found at that point)")
        else:
            t = focus["target"]
            print(f"  TARGET: tag={t['tag']} role={t['role']!r} aria={t['aria']!r} title={t['title']!r}")
            print(f"          href={t['href']!r} data-testid={t['testid']!r} text={t['text']!r} box={t['box']}")
            print(f"          outerHTML={t['outerHTML']!r}")
            print("  ANCESTRY (target → up):")
            for a in focus["ancestry"]:
                print(f"    - {a['tag']} role={a['role']!r} aria={a['aria']!r} testid={a['testid']!r} box={a['box']}")
            print("  DESCENDANTS (labels/icons/links inside target):")
            for k in focus["descendants"]:
                print(f"    - {k['tag']} role={k['role']!r} aria={k['aria']!r} text={k['text']!r}")
            print("  SIBLING COLUMN (nearby nav items — the real Create is likely here):")
            for s in focus["siblingColumn"]:
                print(f"    - {s['tag']} role={s['role']!r} aria={s['aria']!r} title={s['title']!r} "
                      f"href={s['href']!r} text={s['text']!r} box={s['box']}")

        print("\n[probe-cc] READ-ONLY complete. NOTHING was clicked. Paste the full")
        print("           output (and open the screenshot) so the real Create control")
        print("           can be identified by its actual DOM attributes.")
    finally:
        await adapter.close()
        print("[probe-cc] closed cleanly.")


async def cmd_probe_new_post_click(args):
    """READ-ONLY except ONE normal click on svg[aria-label='New post'].

    Proves whether a single, normal Playwright click on the real New Post SVG
    opens the Instagram composer. No force click, no JS click, no coordinate
    click, no fallback locator. Does NOT click Post/upload/publish/prepare/confirm.
    """
    import os

    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")),
            "_diag")
        os.makedirs(shot_dir, exist_ok=True)

        svg = page.locator("svg[aria-label='New post']")
        count = await svg.count()
        print(f"[probe-npc] svg[aria-label='New post'] count = {count}")
        if count == 0:
            print("RESULT: NEW_POST_CLICK_DOES_NOT_OPEN_COMPOSER")
            print("[probe-npc] evidence: the New Post SVG was not found on the page.")
            return
        target = svg.first

        # ── STEP 2: full detail on the SVG target ────────────────────────────
        DETAIL_JS = r"""
        (el) => {
          const r = el.getBoundingClientRect();
          const cs = window.getComputedStyle(el);
          let oh = el.outerHTML || ''; if (oh.length>300) oh = oh.slice(0,300)+'…';
          // elementFromPoint at the SVG center — what actually receives a click.
          const cx = Math.round(r.x + r.width/2), cy = Math.round(r.y + r.height/2);
          const top = document.elementFromPoint(cx, cy);
          const stack = document.elementsFromPoint(cx, cy).slice(0, 6).map(e => ({
            tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
            aria: e.getAttribute('aria-label'), href: e.getAttribute('href'),
            pe: window.getComputedStyle(e).pointerEvents,
          }));
          const desc = (e) => e ? {
            tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
            aria: e.getAttribute('aria-label'), href: e.getAttribute('href'),
            testid: e.getAttribute('data-testid'),
          } : null;
          const anc = []; let p = el.parentElement, d = 0;
          while (p && d < 6) { anc.push(desc(p)); p = p.parentElement; d++; }
          return {
            box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
            pointerEvents: cs.pointerEvents, display: cs.display,
            visibility: cs.visibility, zIndex: cs.zIndex,
            center: {cx, cy},
            elementFromPoint: desc(top),
            elementsFromPoint: stack,
            ancestors: anc,
            outerHTML: oh,
          };
        }"""
        d = await target.evaluate(DETAIL_JS)
        try:
            vis = await target.is_visible()
        except Exception:
            vis = "?"
        try:
            en = await target.is_enabled()
        except Exception:
            en = "n/a (svg)"
        print("\n================ NEW POST SVG — DETAIL (read-only) ================")
        print(f"  box={d['box']} visible={vis} enabled={en}")
        print(f"  computed: pointer-events={d['pointerEvents']} display={d['display']} "
              f"visibility={d['visibility']} z-index={d['zIndex']}")
        print(f"  center={d['center']}")
        print(f"  elementFromPoint(center) = {d['elementFromPoint']}")
        print(f"  elementsFromPoint(center) = {d['elementsFromPoint']}")
        print("  ancestors (svg → up):")
        for a in d["ancestors"]:
            print(f"    - {a}")
        print(f"  outerHTML={d['outerHTML']!r}")

        # ── STEP 6a: snapshot BEFORE ─────────────────────────────────────────
        SNAP_JS = r"""
        () => {
          const q = (s) => document.querySelectorAll(s).length;
          const vis = (s) => Array.from(document.querySelectorAll(s))
            .filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; }).length;
          const words = ['Post','Story','Reel','Live'];
          const overlays = Array.from(document.querySelectorAll("[role=dialog],[role=menu],[role=listbox],[aria-modal=true]"));
          const overlayHits = [];
          for (const c of overlays) {
            for (const it of c.querySelectorAll("a,button,[role=menuitem],[role=button],div[tabindex],span")) {
              const t = (it.innerText||'').trim();
              const r = it.getBoundingClientRect();
              if (t && r.width>0 && r.height>0 && words.some(w => t===w || t.startsWith(w)))
                overlayHits.push(t.slice(0,20));
            }
          }
          return {
            url: location.href,
            dialogsVis: vis("[role=dialog]"), menusVis: vis("[role=menu]"),
            menuitems: q("[role=menuitem]"), listboxes: q("[role=listbox]"),
            fileInputs: q("input[type=file]"),
            bodyLen: document.body.innerHTML.length,
            overlayHits: Array.from(new Set(overlayHits)),
          };
        }"""
        before = await page.evaluate(SNAP_JS)
        print("\n================ SNAPSHOT: BEFORE ================")
        print(f"  {before}")

        # ── STEP 3/4: EXACTLY ONE normal click on the SVG (no force/JS/coords) ─
        print("\n[probe-npc] performing ONE normal Playwright click on the New Post SVG…")
        clicked_ok = True
        click_err = None
        try:
            await target.click(timeout=8000)  # normal click only
        except Exception as e:
            clicked_ok = False
            click_err = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-npc] click dispatched ok = {clicked_ok}" + (f"  error={click_err}" if click_err else ""))

        # ── STEP 5: wait ─────────────────────────────────────────────────────
        await asyncio.sleep(2)

        # ── STEP 6b: snapshot AFTER + diff ───────────────────────────────────
        after = await page.evaluate(SNAP_JS)
        print("\n================ SNAPSHOT: AFTER ================")
        print(f"  {after}")
        print("\n================ BEFORE → AFTER DIFF ================")
        print(f"  url changed:        {before['url'] != after['url']}  ({before['url']!r} -> {after['url']!r})")
        print(f"  visible dialogs:    {before['dialogsVis']} -> {after['dialogsVis']}")
        print(f"  visible menus:      {before['menusVis']} -> {after['menusVis']}")
        print(f"  menuitems:          {before['menuitems']} -> {after['menuitems']}")
        print(f"  listboxes:          {before['listboxes']} -> {after['listboxes']}")
        print(f"  file inputs:        {before['fileInputs']} -> {after['fileInputs']}")
        print(f"  bodyLen delta:      {after['bodyLen'] - before['bodyLen']}")
        print(f"  Post/Story/Reel/Live in overlays: {before['overlayHits']} -> {after['overlayHits']}")

        # ── STEP 7: screenshot AFTER ─────────────────────────────────────────
        shot = os.path.join(shot_dir, "instagram_after_new_post_click.png")
        try:
            await adapter.screenshot(shot)
            print(f"\n[probe-npc] screenshot saved: {shot}")
        except Exception as ex:
            print(f"\n[probe-npc] screenshot failed: {type(ex).__name__}")

        # ── Verdict ──────────────────────────────────────────────────────────
        opened = (after['dialogsVis'] > before['dialogsVis']
                  or after['menusVis'] > before['menusVis']
                  or after['menuitems'] > before['menuitems']
                  or after['listboxes'] > before['listboxes']
                  or after['fileInputs'] > before['fileInputs']
                  or len(after['overlayHits']) > len(before['overlayHits']))
        print("\n" + ("RESULT: NEW_POST_CLICK_OPENS_COMPOSER" if opened
                       else "RESULT: NEW_POST_CLICK_DOES_NOT_OPEN_COMPOSER"))
        if not opened:
            print("[probe-npc] evidence: no new dialog/menu/menuitem/listbox/file-input and no")
            print("            Post/Story/Reel/Live overlay text appeared after a normal click;")
            print("            see the elementFromPoint/pointer-events detail above and the")
            print("            screenshot for what actually received the click.")
        print("[probe-npc] Post NOT clicked, no media, nothing published.")
    finally:
        await adapter.close()
        print("[probe-npc] closed cleanly.")


async def cmd_probe_new_post_parent(args):
    """READ-ONLY except ONE normal click on the New-post PARENT interactive
    element (not the SVG). Determines whether activating the parent nav control
    opens a Create flyout, the composer, or nothing — and, if a flyout opens,
    identifies the exact 'Post' option. No media, no Share, no publish.
    """
    import os

    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")),
            "_diag")
        os.makedirs(shot_dir, exist_ok=True)

        svg = page.locator("svg[aria-label='New post']")
        if await svg.count() == 0:
            print("[probe-npp] svg[aria-label='New post'] not found")
            print("RESULT: PARENT_NEW_POST_DOES_NOT_OPEN_ANYTHING"); return

        # ── STEP 2/3: walk the ancestry, report each level's interactivity, and
        #    pick the nearest ancestor that OWNS the click (a / role=link|button
        #    / has cursor:pointer / has href). Read-only. ─────────────────────
        ANCESTRY_JS = r"""
        (svg) => {
          const desc = (el, idx) => {
            const r = el.getBoundingClientRect();
            const cs = window.getComputedStyle(el);
            let oh = el.outerHTML || ''; if (oh.length>180) oh = oh.slice(0,180)+'…';
            return {
              level: idx,
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role'),
              href: el.getAttribute('href'),
              tabindex: el.getAttribute('tabindex'),
              aria: el.getAttribute('aria-label'),
              testid: el.getAttribute('data-testid'),
              cursor: cs.cursor,
              pointerEvents: cs.pointerEvents,
              box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
              outerHTML: oh,
            };
          };
          const chain = [];
          let el = svg, i = 0;
          while (el && i < 8) { chain.push(desc(el, i)); el = el.parentElement; i++; }
          // Choose the intended click owner: nearest ancestor (incl svg's parent)
          // that is <a>, or role=link/button, or has cursor:pointer, or href.
          let ownerIdx = -1;
          for (let j = 1; j < chain.length; j++) {
            const c = chain[j];
            if (c.tag === 'a' || c.role === 'link' || c.role === 'button'
                || c.cursor === 'pointer' || c.href != null || c.tabindex != null) {
              ownerIdx = j; break;
            }
          }
          return { chain, ownerIdx };
        }"""
        res = await svg.first.evaluate(ANCESTRY_JS)
        print("\n================ NEW POST — INTERACTIVE ANCESTRY (read-only) ================")
        for c in res["chain"]:
            print(f"  L{c['level']}: {c['tag']} role={c['role']!r} href={c['href']!r} "
                  f"tabindex={c['tabindex']!r} cursor={c['cursor']} pe={c['pointerEvents']} box={c['box']}")
            print(f"        aria={c['aria']!r} testid={c['testid']!r}")
            print(f"        outerHTML={c['outerHTML']!r}")
        owner_idx = res["ownerIdx"]
        print(f"\n[probe-npp] chosen click-owner ancestor level = {owner_idx}")
        if owner_idx < 0:
            print("[probe-npp] no interactive ancestor found (no a/role/cursor/href/tabindex).")
            print("RESULT: PARENT_NEW_POST_DOES_NOT_OPEN_ANYTHING"); return

        # Build a Playwright handle to the chosen owner: svg -> xpath ancestor.
        # owner_idx levels up from the svg (level 0 = svg itself).
        owner = svg.first.locator("xpath=" + "/".join([".."] * owner_idx)) if owner_idx > 0 else svg.first
        try:
            otag = await owner.evaluate("e => e.tagName.toLowerCase()")
            ohref = await owner.get_attribute("href")
            orole = await owner.get_attribute("role")
        except Exception:
            otag = ohref = orole = "?"
        print(f"[probe-npp] owner handle: tag={otag} role={orole!r} href={ohref!r}")

        # ── snapshot BEFORE ──────────────────────────────────────────────────
        SNAP_JS = r"""
        () => {
          const q = (s) => document.querySelectorAll(s).length;
          const vis = (s) => Array.from(document.querySelectorAll(s))
            .filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; }).length;
          const words = ['Post','Story','Reel','Live'];
          const overlays = Array.from(document.querySelectorAll("[role=dialog],[role=menu],[role=listbox],[aria-modal=true]"));
          const hits = [];
          for (const c of overlays)
            for (const it of c.querySelectorAll("a,button,[role=menuitem],[role=button],div[tabindex],span")) {
              const t=(it.innerText||'').trim(); const r=it.getBoundingClientRect();
              if (t && r.width>0 && r.height>0 && words.some(w=>t===w||t.startsWith(w)))
                hits.push({text:t.slice(0,20), tag:it.tagName.toLowerCase(), role:it.getAttribute('role'),
                           href:it.getAttribute('href'), container:(c.getAttribute('role')||'aria-modal')});
            }
          return { url: location.href, dialogsVis: vis("[role=dialog]"), menusVis: vis("[role=menu]"),
                   menuitems: q("[role=menuitem]"), listboxes: q("[role=listbox]"),
                   fileInputs: q("input[type=file]"), bodyLen: document.body.innerHTML.length, hits };
        }"""
        before = await page.evaluate(SNAP_JS)
        print(f"\n[probe-npp] BEFORE: {{'url': ..., 'dialogsVis': {before['dialogsVis']}, "
              f"'menusVis': {before['menusVis']}, 'menuitems': {before['menuitems']}, "
              f"'listboxes': {before['listboxes']}, 'fileInputs': {before['fileInputs']}, "
              f"'bodyLen': {before['bodyLen']}, 'overlayHits': {before['hits']}}}")

        # ── STEP 7: ONE normal click on the PARENT owner (not the SVG) ───────
        print(f"\n[probe-npp] performing ONE normal click on the parent owner (level {owner_idx})…")
        click_ok, err = True, None
        try:
            await owner.click(timeout=8000)
        except Exception as e:
            click_ok, err = False, f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-npp] parent click dispatched ok = {click_ok}" + (f" error={err}" if err else ""))
        await asyncio.sleep(2)

        after = await page.evaluate(SNAP_JS)
        print(f"\n[probe-npp] AFTER:  {{'url': ..., 'dialogsVis': {after['dialogsVis']}, "
              f"'menusVis': {after['menusVis']}, 'menuitems': {after['menuitems']}, "
              f"'listboxes': {after['listboxes']}, 'fileInputs': {after['fileInputs']}, "
              f"'bodyLen': {after['bodyLen']}, 'overlayHits': {after['hits']}}}")
        print("\n================ DIFF ================")
        print(f"  url changed:   {before['url'] != after['url']}")
        print(f"  dialogsVis:    {before['dialogsVis']} -> {after['dialogsVis']}")
        print(f"  menusVis:      {before['menusVis']} -> {after['menusVis']}")
        print(f"  menuitems:     {before['menuitems']} -> {after['menuitems']}")
        print(f"  listboxes:     {before['listboxes']} -> {after['listboxes']}")
        print(f"  fileInputs:    {before['fileInputs']} -> {after['fileInputs']}")
        print(f"  bodyLen delta: {after['bodyLen'] - before['bodyLen']}")
        print(f"  overlay Post/Story/Reel/Live: {before['hits']} -> {after['hits']}")

        shot = os.path.join(shot_dir, "instagram_after_parent_click.png")
        try:
            await adapter.screenshot(shot); print(f"\n[probe-npp] screenshot: {shot}")
        except Exception as ex:
            print(f"\n[probe-npp] screenshot failed: {type(ex).__name__}")

        composer_open = (after['dialogsVis'] > before['dialogsVis'] or after['fileInputs'] > before['fileInputs'])
        flyout_open = (after['menusVis'] > before['menusVis'] or after['menuitems'] > before['menuitems']
                       or after['listboxes'] > before['listboxes']
                       or len(after['hits']) > len(before['hits']))

        print("\n================ RESULT ================")
        if composer_open:
            print("RESULT: PARENT_NEW_POST_OPENS_COMPOSER")
            print(f"  concrete element: {otag} (role={orole!r}, href={ohref!r}) at ancestry level {owner_idx}")
        elif flyout_open:
            print("RESULT: PARENT_NEW_POST_OPENS_CREATE_FLYOUT")
            print(f"  concrete element: {otag} (role={orole!r}, href={ohref!r}) at ancestry level {owner_idx}")
            print(f"  flyout Post/Story/Reel/Live options: {after['hits']}")
            print("  (the exact 'Post' option is in the overlayHits above — role/href shown)")
        else:
            print("RESULT: PARENT_NEW_POST_DOES_NOT_OPEN_ANYTHING")
            print(f"  clicked owner: {otag} (role={orole!r}, href={ohref!r}); no overlay/menu/composer appeared.")
            print("  See the ancestry dump above for the event/listener structure.")
        print("[probe-npp] No Post clicked, no media, nothing published.")
    finally:
        await adapter.close()
        print("[probe-npp] closed cleanly.")


async def cmd_probe_new_post_anchor(args):
    """READ-ONLY except ONE normal click on the exact anchor:
        svg[aria-label='New post'] -> closest("a[href='#']")

    Determines whether activating THAT anchor opens a Create flyout, the
    composer, or nothing. No generic heuristic, no fallback, no media, no Post
    click, no publish.
    """
    import os

    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")),
            "_diag")
        os.makedirs(shot_dir, exist_ok=True)

        # ── STEP 1: exactly one anchor via svg.closest("a[href='#']") ────────
        # Playwright can't call closest() directly in a selector, so resolve the
        # anchor by evaluating from the SVG and count matches explicitly.
        svg = page.locator("svg[aria-label='New post']")
        svg_count = await svg.count()
        print(f"[probe-npa] svg[aria-label='New post'] count = {svg_count}")
        if svg_count == 0:
            print("ANCHOR_NEW_POST_DOES_NOT_OPEN_ANYTHING")
            print("[probe-npa] evidence: New Post SVG not present."); return

        ANCHOR_JS = r"""
        (svg) => {
          const a = svg.closest("a[href='#']");
          if (!a) return { found: false };
          const r = a.getBoundingClientRect();
          const cs = window.getComputedStyle(a);
          let oh = a.outerHTML || ''; if (oh.length>300) oh = oh.slice(0,300)+'…';
          // How many anchors on the page match this exact closest relationship
          // from ANY 'New post' svg (should be exactly 1).
          const all = Array.from(document.querySelectorAll("svg[aria-label='New post']"))
            .map(s => s.closest("a[href='#']")).filter(Boolean);
          const uniq = new Set(all);
          return {
            found: true,
            matchCount: uniq.size,
            role: a.getAttribute('role'),
            href: a.getAttribute('href'),
            tabindex: a.getAttribute('tabindex'),
            cursor: cs.cursor,
            box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
            outerHTML: oh,
          };
        }"""
        info = await svg.first.evaluate(ANCHOR_JS)
        if not info.get("found"):
            print("[probe-npa] svg.closest(\"a[href='#']\") found NO anchor.")
            print("ANCHOR_NEW_POST_DOES_NOT_OPEN_ANYTHING")
            print("[probe-npa] evidence: the New Post SVG has no ancestor a[href='#'].")
            return

        print("\n================ TARGET ANCHOR (svg.closest(a[href='#'])) ================")
        print(f"  matching anchors (should be 1) = {info['matchCount']}")
        print(f"  role={info['role']!r} href={info['href']!r} tabindex={info['tabindex']!r} cursor={info['cursor']}")
        print(f"  box={info['box']}")
        print(f"  outerHTML={info['outerHTML']!r}")

        # A Playwright handle to that exact anchor: the <a href='#'> that is an
        # ancestor of THIS svg. Scope by `has` so we pick the right one.
        anchor = page.locator("a[href='#']").filter(has=page.locator("svg[aria-label='New post']"))
        acount = await anchor.count()
        print(f"[probe-npa] Playwright anchor handle count = {acount}")
        if acount != 1:
            print("[probe-npa] WARNING: expected exactly 1 anchor handle.")

        # ── STEP 3: snapshot BEFORE ──────────────────────────────────────────
        SNAP_JS = r"""
        () => {
          const q = (s) => document.querySelectorAll(s).length;
          const vis = (s) => Array.from(document.querySelectorAll(s))
            .filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; }).length;
          const words = ['Post','Story','Reel','Live'];
          const overlays = Array.from(document.querySelectorAll("[role=dialog],[role=menu],[role=listbox],[aria-modal=true]"));
          const hits = [];
          for (const c of overlays)
            for (const it of c.querySelectorAll("a,button,[role=menuitem],[role=button],div[tabindex],span")) {
              const t=(it.innerText||'').trim(); const r=it.getBoundingClientRect();
              if (t && r.width>0 && r.height>0 && words.some(w=>t===w||t.startsWith(w)))
                hits.push({text:t.slice(0,24), tag:it.tagName.toLowerCase(), role:it.getAttribute('role'),
                           href:it.getAttribute('href'), testid:it.getAttribute('data-testid'),
                           container:(c.getAttribute('role')||'aria-modal')});
            }
          return { url: location.href, dialogsVis: vis("[role=dialog]"), menusVis: vis("[role=menu]"),
                   menuitems: q("[role=menuitem]"), listboxes: q("[role=listbox]"),
                   fileInputs: q("input[type=file]"), bodyLen: document.body.innerHTML.length, hits };
        }"""
        before = await page.evaluate(SNAP_JS)
        print(f"\n[probe-npa] BEFORE: dialogsVis={before['dialogsVis']} menusVis={before['menusVis']} "
              f"menuitems={before['menuitems']} listboxes={before['listboxes']} fileInputs={before['fileInputs']} "
              f"bodyLen={before['bodyLen']} overlayHits={before['hits']}")

        # ── STEP 4: ONE normal Playwright click on the anchor ────────────────
        print("\n[probe-npa] performing ONE normal click on the target anchor…")
        click_ok, err = True, None
        try:
            await anchor.first.click(timeout=8000)
        except Exception as e:
            click_ok, err = False, f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-npa] anchor click dispatched ok = {click_ok}" + (f" error={err}" if err else ""))
        await asyncio.sleep(2)

        # ── STEP 5/6/7: snapshot AFTER + diff ────────────────────────────────
        after = await page.evaluate(SNAP_JS)
        print(f"\n[probe-npa] AFTER:  dialogsVis={after['dialogsVis']} menusVis={after['menusVis']} "
              f"menuitems={after['menuitems']} listboxes={after['listboxes']} fileInputs={after['fileInputs']} "
              f"bodyLen={after['bodyLen']} overlayHits={after['hits']}")
        print("\n================ DIFF ================")
        print(f"  url changed:   {before['url'] != after['url']}")
        print(f"  dialogsVis:    {before['dialogsVis']} -> {after['dialogsVis']}")
        print(f"  menusVis:      {before['menusVis']} -> {after['menusVis']}")
        print(f"  menuitems:     {before['menuitems']} -> {after['menuitems']}")
        print(f"  listboxes:     {before['listboxes']} -> {after['listboxes']}")
        print(f"  fileInputs:    {before['fileInputs']} -> {after['fileInputs']}")
        print(f"  bodyLen delta: {after['bodyLen'] - before['bodyLen']}")
        print(f"  overlay Post/Story/Reel/Live: {before['hits']} -> {after['hits']}")

        # ── STEP 8: screenshot ───────────────────────────────────────────────
        shot = os.path.join(shot_dir, "instagram_after_anchor_click.png")
        try:
            await adapter.screenshot(shot); print(f"\n[probe-npa] screenshot: {shot}")
        except Exception as ex:
            print(f"\n[probe-npa] screenshot failed: {type(ex).__name__}")

        # ── STEP 9: single final result ──────────────────────────────────────
        composer_open = (after['dialogsVis'] > before['dialogsVis'] or after['fileInputs'] > before['fileInputs'])
        flyout_open = (after['menusVis'] > before['menusVis'] or after['menuitems'] > before['menuitems']
                       or after['listboxes'] > before['listboxes'] or len(after['hits']) > len(before['hits']))
        print("\n================ RESULT ================")
        if composer_open:
            print("ANCHOR_NEW_POST_OPENS_COMPOSER")
        elif flyout_open:
            print("ANCHOR_NEW_POST_OPENS_CREATE_FLYOUT")
            print(f"  flyout Post/Story/Reel/Live options (do NOT click yet): {after['hits']}")
        else:
            print("ANCHOR_NEW_POST_DOES_NOT_OPEN_ANYTHING")
            print("  clicked the real a[href='#'] anchor; no overlay/menu/composer/file-input appeared.")
        print("[probe-npa] No Post clicked, no media, nothing published.")
    finally:
        await adapter.close()
        print("[probe-npa] closed cleanly.")


async def cmd_probe_new_post_anchor_inspect(args):
    """READ-ONLY except ONE normal click on the confirmed New-post anchor
    (svg[aria-label='New post'].closest("a[href='#']")).

    Captures a true BEFORE/AFTER visible-DOM diff and reports EXACTLY what UI
    appeared — without assuming any ARIA role or label. No media, no Share, no
    Post click, no workflow change.
    """
    import os

    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")),
            "_diag")
        os.makedirs(shot_dir, exist_ok=True)

        svg = page.locator("svg[aria-label='New post']")
        if await svg.count() == 0:
            print("[probe-npai] New Post SVG not found")
            print("RESULT: NEW_POST_NO_VISIBLE_UI_CHANGE"); return
        anchor = page.locator("a[href='#']").filter(has=page.locator("svg[aria-label='New post']"))
        anchor_found = (await anchor.count()) == 1
        print(f"[probe-npai] confirmed anchor found = {anchor_found}")

        # Signature JS: snapshot the set of VISIBLE actionable/labelled elements
        # (keyed identity) + counts, so we can diff BEFORE vs AFTER precisely.
        SNAP_JS = r"""
        () => {
          const isVis = (e) => {
            const r = e.getBoundingClientRect();
            const cs = window.getComputedStyle(e);
            return r.width>0 && r.height>0 && cs.visibility!=='hidden' && cs.display!=='none';
          };
          const key = (e) => {
            const r = e.getBoundingClientRect();
            return e.tagName + '|' + (e.getAttribute('aria-label')||'') + '|' +
                   ((e.innerText||'').trim().slice(0,20)) + '|' +
                   Math.round(r.x)+','+Math.round(r.y)+','+Math.round(r.width)+'x'+Math.round(r.height);
          };
          const sel = "button,a,[role=button],[role=link],[role=dialog],input,textarea,[contenteditable=true],h1,h2,h3,[aria-label]";
          const items = {};
          for (const e of document.querySelectorAll(sel)) {
            if (!isVis(e)) continue;
            items[key(e)] = 1;
          }
          return {
            url: location.href,
            bodyLen: document.body.innerHTML.length,
            fileInputs: document.querySelectorAll("input[type=file]").length,
            keys: items,
          };
        }"""
        before = await page.evaluate(SNAP_JS)
        print(f"\n[probe-npai] BEFORE: url ok, bodyLen={before['bodyLen']} "
              f"visibleTracked={len(before['keys'])} fileInputs={before['fileInputs']}")

        # ── ONE normal click ─────────────────────────────────────────────────
        print("[probe-npai] performing ONE normal click on the confirmed anchor…")
        click_ok, err = True, None
        try:
            await anchor.first.click(timeout=8000)
        except Exception as e:
            click_ok, err = False, f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-npai] click dispatched ok = {click_ok}" + (f" error={err}" if err else ""))
        await asyncio.sleep(2)

        # Detailed report of NEWLY-visible elements (keys not present before).
        DETAIL_JS = r"""
        (beforeKeys) => {
          const isVis = (e) => {
            const r = e.getBoundingClientRect();
            const cs = window.getComputedStyle(e);
            return r.width>0 && r.height>0 && cs.visibility!=='hidden' && cs.display!=='none';
          };
          const key = (e) => {
            const r = e.getBoundingClientRect();
            return e.tagName + '|' + (e.getAttribute('aria-label')||'') + '|' +
                   ((e.innerText||'').trim().slice(0,20)) + '|' +
                   Math.round(r.x)+','+Math.round(r.y)+','+Math.round(r.width)+'x'+Math.round(r.height);
          };
          const desc = (e) => {
            const r = e.getBoundingClientRect();
            let oh = e.outerHTML||''; if (oh.length>220) oh=oh.slice(0,220)+'…';
            return {
              tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
              aria: e.getAttribute('aria-label'),
              text: (e.innerText||'').trim().slice(0,50),
              testid: e.getAttribute('data-testid'), href: e.getAttribute('href'),
              type: e.getAttribute('type'),
              box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
              enabled: !e.disabled,
              outerHTML: oh,
            };
          };
          const before = new Set(beforeKeys);
          const sel = "button,a,[role=button],[role=link],[role=dialog],input,textarea,[contenteditable=true],h1,h2,h3,[aria-label]";
          const news = [];
          for (const e of document.querySelectorAll(sel)) {
            if (!isVis(e)) continue;
            if (before.has(key(e))) continue;
            news.push(desc(e));
          }
          // Keyword scan across the WHOLE visible DOM (not just ARIA overlays).
          const terms = ['Create new post','Drag photos and videos here','Select from computer',
            'Select from your computer','Upload','Create','Post','Story','Reel','Live','Cancel','Next','Discard'];
          const bodyText = document.body.innerText || '';
          const present = terms.filter(t => bodyText.includes(t));
          // Newly-visible large overlay-ish containers (fixed/absolute, big box),
          // even WITHOUT role=dialog.
          const overlays = [];
          for (const e of document.querySelectorAll("div,section")) {
            if (!isVis(e)) continue;
            const cs = window.getComputedStyle(e);
            const r = e.getBoundingClientRect();
            if ((cs.position==='fixed'||cs.position==='absolute') && r.width>=320 && r.height>=320) {
              if (before.has(key(e))) continue;
              let oh = e.outerHTML||''; if (oh.length>160) oh=oh.slice(0,160)+'…';
              overlays.push({tag:e.tagName.toLowerCase(), role:e.getAttribute('role'),
                aria:e.getAttribute('aria-label'), zIndex:cs.zIndex,
                box:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}, oh});
            }
          }
          const fileInputs = Array.from(document.querySelectorAll("input[type=file]")).map(e => ({
            visible: isVis(e), accept: e.getAttribute('accept'),
          }));
          return { news, present, overlays, fileInputs };
        }"""
        after = await page.evaluate(SNAP_JS)
        detail = await page.evaluate(DETAIL_JS, list(before["keys"].keys()))

        body_delta = after["bodyLen"] - before["bodyLen"]
        print(f"\n[probe-npai] AFTER: bodyLen={after['bodyLen']} (delta {body_delta}) "
              f"fileInputs={after['fileInputs']}")

        print("\n================ NEWLY-VISIBLE ELEMENTS (not present before) ================")
        if not detail["news"]:
            print("  (none)")
        for i, e in enumerate(detail["news"][:40]):
            print(f"  [{i}] tag={e['tag']} role={e['role']!r} aria={e['aria']!r} type={e['type']!r} "
                  f"enabled={e['enabled']} box={e['box']}")
            print(f"       text={e['text']!r} data-testid={e['testid']!r} href={e['href']!r}")
            print(f"       outerHTML={e['outerHTML']!r}")

        print("\n================ NEWLY-VISIBLE OVERLAY-LIKE CONTAINERS (no role required) ================")
        if not detail["overlays"]:
            print("  (none)")
        for o in detail["overlays"][:10]:
            print(f"  tag={o['tag']} role={o['role']!r} aria={o['aria']!r} zIndex={o['zIndex']} box={o['box']}")
            print(f"     outerHTML={o['oh']!r}")

        print("\n================ COMPOSER KEYWORD SCAN (whole visible body) ================")
        print(f"  present terms: {detail['present']}")

        print("\n================ FILE INPUTS ================")
        print(f"  before={before['fileInputs']} after={after['fileInputs']} details={detail['fileInputs']}")

        # ── Screenshot ───────────────────────────────────────────────────────
        shot = os.path.join(shot_dir, "instagram_after_new_post_anchor_inspect.png")
        try:
            await adapter.screenshot(shot); print(f"\n[probe-npai] screenshot: {shot}")
        except Exception as ex:
            shot = f"(failed: {type(ex).__name__})"
            print(f"\n[probe-npai] screenshot failed: {type(ex).__name__}")

        # ── Result classification (NOT the old dialog/menu-only criteria) ────
        COMPOSER_TERMS = {"Create new post", "Drag photos and videos here",
                          "Select from computer", "Select from your computer"}
        composer_indicators = [t for t in detail["present"] if t in COMPOSER_TERMS]
        composer_evidence = bool(composer_indicators) or after["fileInputs"] > before["fileInputs"]
        new_ui = bool(detail["news"]) or bool(detail["overlays"]) or body_delta > 500

        if composer_evidence:
            result = "RESULT: NEW_POST_OPENED_COMPOSER"
        elif new_ui:
            result = "RESULT: NEW_POST_OPENED_OTHER_UI"
        else:
            result = "RESULT: NEW_POST_NO_VISIBLE_UI_CHANGE"
        print("\n" + result)

        print("\n================ DIAGNOSTIC SUMMARY ================")
        print(f"  anchor found:            {anchor_found}")
        print(f"  click dispatched:        {click_ok}")
        print(f"  body length delta:       {body_delta}")
        print(f"  newly visible elements:  {len(detail['news'])} (overlays: {len(detail['overlays'])})")
        print(f"  likely composer terms:   {composer_indicators or 'none'}")
        print(f"  file input count:        {before['fileInputs']} -> {after['fileInputs']}")
        print(f"  screenshot path:         {shot}")
        print(f"  {result}")
        print("\n[probe-npai] No upload control clicked, no media, nothing published.")
    finally:
        await adapter.close()
        print("[probe-npai] closed cleanly.")


async def cmd_probe_post_item_click(args):
    """READ-ONLY except TWO normal clicks proving the full open path:
        New post: svg[aria-label='New post'] -> closest("a[href='#']")
        Flyout Post: svg[aria-label='Post']  -> closest("a[href='#']")

    Determines whether clicking the flyout Post item opens the composer. Uses
    ONLY the confirmed svg.closest(a[href='#']) relationships — no generic text
    or get_by_role('link','Post') selectors, no fallback, no retries. No media,
    no Share, no workflow change.
    """
    import os

    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)

        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")),
            "_diag")
        os.makedirs(shot_dir, exist_ok=True)

        SNAP_JS = r"""
        () => {
          const q = (s) => document.querySelectorAll(s).length;
          const vis = (s) => Array.from(document.querySelectorAll(s))
            .filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; }).length;
          const isVis = (e) => { const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
            return r.width>0 && r.height>0 && cs.visibility!=='hidden' && cs.display!=='none'; };
          const key = (e) => { const r=e.getBoundingClientRect();
            return e.tagName+'|'+(e.getAttribute('aria-label')||'')+'|'+((e.innerText||'').trim().slice(0,20))+'|'+
                   Math.round(r.x)+','+Math.round(r.y)+','+Math.round(r.width)+'x'+Math.round(r.height); };
          const sel = "button,a,[role=button],[role=link],[role=dialog],input,textarea,[contenteditable=true],h1,h2,h3,[aria-label]";
          const keys = {};
          for (const e of document.querySelectorAll(sel)) if (isVis(e)) keys[key(e)] = 1;
          return { url: location.href, bodyLen: document.body.innerHTML.length,
                   dialogsVis: vis("[role=dialog]"), menusVis: vis("[role=menu]"),
                   menuitems: q("[role=menuitem]"), listboxes: q("[role=listbox]"),
                   fileInputs: q("input[type=file]"), keys };
        }"""
        DETAIL_JS = r"""
        (beforeKeys) => {
          const before = new Set(beforeKeys);
          const isVis = (e) => { const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
            return r.width>0 && r.height>0 && cs.visibility!=='hidden' && cs.display!=='none'; };
          const key = (e) => { const r=e.getBoundingClientRect();
            return e.tagName+'|'+(e.getAttribute('aria-label')||'')+'|'+((e.innerText||'').trim().slice(0,20))+'|'+
                   Math.round(r.x)+','+Math.round(r.y)+','+Math.round(r.width)+'x'+Math.round(r.height); };
          const desc = (e) => { const r=e.getBoundingClientRect(); let oh=e.outerHTML||''; if(oh.length>220) oh=oh.slice(0,220)+'…';
            return { tag:e.tagName.toLowerCase(), role:e.getAttribute('role'), aria:e.getAttribute('aria-label'),
                     text:(e.innerText||'').trim().slice(0,50), testid:e.getAttribute('data-testid'),
                     href:e.getAttribute('href'), type:e.getAttribute('type'),
                     box:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
                     enabled:!e.disabled, outerHTML:oh }; };
          const sel = "button,a,[role=button],[role=link],[role=dialog],input,textarea,[contenteditable=true],h1,h2,h3,[aria-label]";
          const news = [];
          for (const e of document.querySelectorAll(sel)) { if (!isVis(e)) continue; if (before.has(key(e))) continue; news.push(desc(e)); }
          const terms = ['Create new post','Drag photos and videos here','Select from computer',
            'Select from your computer','Upload','Next','Cancel','Discard','Create','Post','Story','Reel','Live'];
          const bodyText = document.body.innerText || '';
          const present = terms.filter(t => bodyText.includes(t));
          const overlays = [];
          for (const e of document.querySelectorAll("div,section")) { if (!isVis(e)) continue;
            const cs=getComputedStyle(e); const r=e.getBoundingClientRect();
            if ((cs.position==='fixed'||cs.position==='absolute') && r.width>=320 && r.height>=320 && !before.has(key(e))) {
              let oh=e.outerHTML||''; if(oh.length>150) oh=oh.slice(0,150)+'…';
              overlays.push({tag:e.tagName.toLowerCase(), role:e.getAttribute('role'), zIndex:cs.zIndex,
                box:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}, oh}); } }
          const fileInputs = Array.from(document.querySelectorAll("input[type=file]")).map(e => ({visible:isVis(e), accept:e.getAttribute('accept')}));
          return { news, present, overlays, fileInputs };
        }"""

        # ── STEP 2: confirmed New Post anchor ────────────────────────────────
        new_post_svg = page.locator("svg[aria-label='New post']")
        new_post_anchor = page.locator("a[href='#']").filter(has=page.locator("svg[aria-label='New post']"))
        np_found = (await new_post_svg.count() >= 1 and await new_post_anchor.count() == 1)
        print(f"[probe-pic] New Post anchor found = {np_found}")
        if not np_found:
            print("RESULT: POST_ITEM_DOES_NOT_OPEN_ANYTHING")
            print("[probe-pic] evidence: New Post anchor not resolvable."); return

        home_snap = await page.evaluate(SNAP_JS)

        # ── STEP 4: click New Post ───────────────────────────────────────────
        print("[probe-pic] clicking New Post anchor (1/2)…")
        np_click_ok = True
        try:
            await new_post_anchor.first.click(timeout=8000)
        except Exception as e:
            np_click_ok = False
            print(f"[probe-pic] New Post click error: {type(e).__name__}: {str(e)[:120]}")
        await asyncio.sleep(2)

        # ── STEP 6/7: resolve the flyout Post anchor via svg[aria-label='Post'] ─
        post_svg = page.locator("svg[aria-label='Post']")
        post_anchor = page.locator("a[href='#']").filter(has=page.locator("svg[aria-label='Post']"))
        post_svg_count = await post_svg.count()
        post_anchor_count = await post_anchor.count()
        print(f"[probe-pic] svg[aria-label='Post'] count = {post_svg_count}  "
              f"post flyout anchor count = {post_anchor_count}")
        if post_anchor_count == 0:
            print("RESULT: POST_ITEM_DOES_NOT_OPEN_ANYTHING")
            print("[probe-pic] evidence: no a[href='#'] containing svg[aria-label='Post'] after opening flyout.")
            return
        if post_anchor_count != 1:
            print(f"[probe-pic] WARNING: expected exactly 1 Post flyout anchor, got {post_anchor_count}. Using .first.")

        # ── STEP 8: describe the Post anchor ─────────────────────────────────
        pd = await post_anchor.first.evaluate(
            "(e)=>{const r=e.getBoundingClientRect();let oh=e.outerHTML||'';if(oh.length>300)oh=oh.slice(0,300)+'…';"
            "return {tag:e.tagName.toLowerCase(),role:e.getAttribute('role'),href:e.getAttribute('href'),"
            "text:(e.innerText||'').trim().slice(0,40),aria:e.getAttribute('aria-label'),"
            "box:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},outerHTML:oh};}"
        )
        pvis = await post_anchor.first.is_visible()
        print("\n================ POST FLYOUT ANCHOR ================")
        print(f"  tag={pd['tag']} role={pd['role']!r} href={pd['href']!r} text={pd['text']!r} aria={pd['aria']!r}")
        print(f"  visible={pvis} box={pd['box']}")
        print(f"  outerHTML={pd['outerHTML']!r}")

        # ── STEP 9: snapshot immediately before clicking Post ────────────────
        before = await page.evaluate(SNAP_JS)
        print(f"\n[probe-pic] BEFORE Post click: bodyLen={before['bodyLen']} dialogsVis={before['dialogsVis']} "
              f"menusVis={before['menusVis']} menuitems={before['menuitems']} listboxes={before['listboxes']} "
              f"fileInputs={before['fileInputs']}")

        # ── STEP 10: ONE normal click on the exact Post anchor ───────────────
        print("[probe-pic] clicking Post flyout anchor (2/2)…")
        post_click_ok, err = True, None
        try:
            await post_anchor.first.click(timeout=8000)
        except Exception as e:
            post_click_ok, err = False, f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-pic] Post click dispatched ok = {post_click_ok}" + (f" error={err}" if err else ""))
        await asyncio.sleep(2)

        # ── STEP 12/13/14: inspect resulting UI ──────────────────────────────
        after = await page.evaluate(SNAP_JS)
        detail = await page.evaluate(DETAIL_JS, list(before["keys"].keys()))
        body_delta = after["bodyLen"] - before["bodyLen"]
        print(f"\n[probe-pic] AFTER Post click: bodyLen={after['bodyLen']} (delta {body_delta}) "
              f"dialogsVis={after['dialogsVis']} menusVis={after['menusVis']} menuitems={after['menuitems']} "
              f"listboxes={after['listboxes']} fileInputs={after['fileInputs']}")

        print("\n================ NEWLY-VISIBLE ELEMENTS (after Post click) ================")
        if not detail["news"]:
            print("  (none)")
        for i, e in enumerate(detail["news"][:40]):
            print(f"  [{i}] tag={e['tag']} role={e['role']!r} aria={e['aria']!r} type={e['type']!r} "
                  f"enabled={e['enabled']} box={e['box']}")
            print(f"       text={e['text']!r} data-testid={e['testid']!r} href={e['href']!r}")
            print(f"       outerHTML={e['outerHTML']!r}")

        print("\n================ NEWLY-VISIBLE OVERLAY-LIKE CONTAINERS ================")
        if not detail["overlays"]:
            print("  (none)")
        for o in detail["overlays"][:10]:
            print(f"  tag={o['tag']} role={o['role']!r} zIndex={o['zIndex']} box={o['box']}")
            print(f"     outerHTML={o['oh']!r}")

        print("\n================ COMPOSER KEYWORD SCAN (whole visible body) ================")
        print(f"  present terms: {detail['present']}")
        print("\n================ FILE INPUTS ================")
        print(f"  before={before['fileInputs']} after={after['fileInputs']} details={detail['fileInputs']}")

        shot = os.path.join(shot_dir, "instagram_after_post_item_click.png")
        try:
            await adapter.screenshot(shot); print(f"\n[probe-pic] screenshot: {shot}")
        except Exception as ex:
            shot = f"(failed: {type(ex).__name__})"
            print(f"\n[probe-pic] screenshot failed: {type(ex).__name__}")

        # ── Result classification (composer-specific evidence, not ARIA-only) ─
        COMPOSER_TERMS = {"Create new post", "Drag photos and videos here",
                          "Select from computer", "Select from your computer"}
        composer_indicators = [t for t in detail["present"] if t in COMPOSER_TERMS]
        composer_evidence = bool(composer_indicators) or after["fileInputs"] > before["fileInputs"]
        new_ui = bool(detail["news"]) or bool(detail["overlays"]) or body_delta > 500

        if composer_evidence:
            result = "RESULT: POST_ITEM_OPENS_COMPOSER"
        elif new_ui:
            result = "RESULT: POST_ITEM_OPENS_OTHER_UI"
        else:
            result = "RESULT: POST_ITEM_DOES_NOT_OPEN_ANYTHING"
        print("\n" + result)

        print("\n================ POST ITEM DIAGNOSTIC SUMMARY ================")
        print(f"  New Post anchor found:   {np_found}")
        print(f"  New Post click dispatched: {np_click_ok}")
        print(f"  Post flyout anchor found: {post_anchor_count == 1} (count={post_anchor_count})")
        print(f"  Post click dispatched:   {post_click_ok}")
        print(f"  body length delta:       {body_delta}")
        print(f"  composer indicators:     {composer_indicators or 'none'}")
        print(f"  file input count:        {before['fileInputs']} -> {after['fileInputs']}")
        print(f"  screenshot path:         {shot}")
        print(f"  {result}")
        print("\n[probe-pic] No upload control clicked, no media, nothing published.")
    finally:
        await adapter.close()
        print("[probe-pic] closed cleanly.")


async def cmd_probe_media_attach(args):
    """READ-ONLY diagnostic for the media-attach failure. Opens the composer via
    the confirmed path, enumerates file inputs, attaches the image the way
    production does (and also tries each input individually), and reports what
    actually registers / previews. Stops BEFORE Next/Share. No publish.

    Requires --media (defaults to the known sigma logo path).
    """
    import os
    from app.social_publishing.providers.hermes.instagram.workflow import HermesInstagramWorkflow

    media = args.media or r"C:\Users\devan\Downloads\sigma_logo.png"
    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()

        # ── 1. File existence/readability (host side) ───────────────────────
        exists = os.path.exists(media)
        isfile = os.path.isfile(media)
        size = os.path.getsize(media) if exists and isfile else None
        readable = False
        if exists and isfile:
            try:
                with open(media, "rb") as f:
                    f.read(16)
                readable = True
            except Exception:
                readable = False
        print(f"[probe-ma] media path = {media}")
        print(f"[probe-ma] exists={exists} isfile={isfile} readable={readable} size={size}")
        if not (exists and isfile and readable):
            print("RESULT: MEDIA_FILE_NOT_READABLE"); return

        # ── 2. Open the composer via the confirmed workflow method ──────────
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)
        wf = HermesInstagramWorkflow()
        opened = await wf._open_composer(adapter)
        print(f"[probe-ma] composer opened (workflow._open_composer) = {opened}")
        await asyncio.sleep(2)

        async def dump_file_inputs(tag):
            fi = page.locator("input[type=file]")
            n = await fi.count()
            print(f"\n  [{tag}] input[type=file] DOM count = {n}")
            rows = []
            for i in range(n):
                el = fi.nth(i)
                try:
                    d = await el.evaluate(
                        "(e)=>({accept:e.getAttribute('accept'),multiple:e.multiple,"
                        "disabled:e.disabled,filesLen:(e.files?e.files.length:-1),"
                        "visible:!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length)})"
                    )
                except Exception as ex:
                    d = {"err": type(ex).__name__}
                print(f"     input[{i}] {d}")
                rows.append(d)
            return n, rows

        n_before, _ = await dump_file_inputs("after composer open")
        print(f"[selector used by production] = {C.IMAGE_FILE_INPUT_SELECTOR!r} (targets .first)")

        async def preview_signals(tag):
            # Report multiple possible preview signals (not just blob img).
            sigs = {}
            for label, sel in (
                ("blob_img", "img[src^='blob:']"),
                ("data_img", "img[src^='data:']"),
                ("any_blob_src", "[src*='blob:']"),
                ("bg_blob", "[style*='blob:']"),
                ("canvas", "canvas"),
                ("Next_btn_role", None),
            ):
                if sel is None:
                    continue
                try:
                    sigs[label] = await page.locator(sel).count()
                except Exception:
                    sigs[label] = "err"
            # Next button presence (composer advances once media attached).
            try:
                nxt = await page.get_by_role("button", name="Next").count()
            except Exception:
                nxt = "err"
            print(f"  [{tag}] preview signals = {sigs} next_button={nxt}")
            return sigs

        print("\n================ BEFORE ATTACH ================")
        await preview_signals("before")

        # ── 3/5/6. Attach exactly as production does: selector .first ───────
        print("\n[probe-ma] attaching via production path: set_input_files('input[type=file]'.first)…")
        prod_err = None
        try:
            await page.locator("input[type=file]").first.set_input_files(media)
        except Exception as e:
            prod_err = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-ma] production attach error = {prod_err}")
        await asyncio.sleep(3)
        # Did the .first input actually take the file?
        try:
            first_files = await page.locator("input[type=file]").first.evaluate(
                "(e)=> e.files ? e.files.length : -1")
        except Exception:
            first_files = "err"
        print(f"[probe-ma] .first input files.length after attach = {first_files}")
        print("\n================ AFTER PRODUCTION ATTACH (.first) ================")
        await preview_signals("after .first")
        prod_attached = await wf._image_attached(adapter)
        print(f"[probe-ma] workflow._image_attached() = {prod_attached}  "
              f"(selector {C.IMAGE_PREVIEW_SELECTOR!r})")

        # ── 7/8. If production path produced no preview, try EACH input ─────
        if not prod_attached:
            print("\n[probe-ma] production path produced no preview — trying each file input individually…")
            fi = page.locator("input[type=file]")
            for i in range(await fi.count()):
                try:
                    await fi.nth(i).set_input_files(media)
                    await asyncio.sleep(2)
                    fl = await fi.nth(i).evaluate("(e)=> e.files ? e.files.length : -1")
                    blob = await page.locator("img[src^='blob:']").count()
                    nxt = await page.get_by_role("button", name="Next").count()
                    print(f"  input[{i}]: files.length={fl} blob_img={blob} next_button={nxt}")
                except Exception as ex:
                    print(f"  input[{i}]: set error {type(ex).__name__}")

        # Screenshot for visual confirmation.
        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")), "_diag")
        os.makedirs(shot_dir, exist_ok=True)
        shot = os.path.join(shot_dir, "instagram_after_media_attach.png")
        try:
            await adapter.screenshot(shot); print(f"\n[probe-ma] screenshot: {shot}")
        except Exception as ex:
            print(f"\n[probe-ma] screenshot failed: {type(ex).__name__}")

        print("\n================ MEDIA ATTACH DIAGNOSTIC SUMMARY ================")
        print(f"  file readable:              {readable} (size={size})")
        print(f"  composer opened:            {opened}")
        print(f"  file inputs after open:     {n_before}")
        print(f"  production selector:        {C.IMAGE_FILE_INPUT_SELECTOR!r} (.first)")
        print(f"  production attach error:    {prod_err}")
        print(f"  .first input accepted file: {first_files}")
        print(f"  preview signal (_image_attached): {prod_attached}")
        print("  (if files.length>0 but no preview → attach-success signal is wrong;")
        print("   if a DIFFERENT input index showed blob_img/Next → wrong input targeted)")
        print("\n[probe-ma] No Next/Share clicked, nothing published.")
    finally:
        await adapter.close()
        print("[probe-ma] closed cleanly.")


async def cmd_probe_caption(args):
    """READ-ONLY diagnostic for the caption-entry failure ("Could not enter the
    Instagram caption"). Opens the composer via the confirmed path, attaches the
    image exactly as production does, then INSPECTS the caption field candidates
    and attempts the SAME production caption-entry mechanism in a reporting-only
    way (describe/resolve), reporting before/after DOM state.

    STRICT SAFETY (per request):
      * Does NOT click Next.
      * Does NOT click Share.
      * Does NOT publish.
      * Does NOT actually type into / mutate the caption field — it runs the
        production resolution (is_visible / _resolve_visible) and DESCRIBES what
        resolves, without calling .fill(). A dry run only.

    IMPORTANT: production's _advance_to_caption() clicks "Next" (crop -> edit ->
    caption) to REVEAL the caption field. Because this probe must NOT click Next,
    it inspects the composer in its immediate post-attach state. The report will
    make clear whether the caption candidates exist at that point, and enumerate
    EVERY editable element present so we can see what the field actually is.

    Requires --media (defaults to the known sigma logo path).
    """
    import os
    from app.social_publishing.providers.hermes.instagram.workflow import HermesInstagramWorkflow

    media = args.media or r"C:\Users\devan\Downloads\sigma_logo.png"
    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()

        # ── 1. Host-side file sanity (same as media-attach probe) ───────────
        exists = os.path.exists(media)
        isfile = os.path.isfile(media)
        size = os.path.getsize(media) if exists and isfile else None
        print(f"[probe-cap] media path = {media}")
        print(f"[probe-cap] exists={exists} isfile={isfile} size={size}")
        if not (exists and isfile):
            print("RESULT: MEDIA_FILE_NOT_FOUND"); return

        # ── 2. Open composer + attach exactly as production does ────────────
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)
        wf = HermesInstagramWorkflow()
        opened = await wf._open_composer(adapter)
        print(f"[probe-cap] composer opened = {opened}")
        if not opened:
            print("RESULT: COMPOSER_DID_NOT_OPEN (cannot inspect caption)"); return
        await asyncio.sleep(2)

        # Attach via the production selector/path.
        attach_err = None
        try:
            await page.locator(C.IMAGE_FILE_INPUT_SELECTOR).first.set_input_files(media)
        except Exception as e:
            attach_err = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-cap] attach error = {attach_err}")
        await asyncio.sleep(3)
        attached = await wf._image_attached(adapter)
        print(f"[probe-cap] _image_attached() = {attached}")

        # ── 3. Report the production caption locators ───────────────────────
        print("\n================ PRODUCTION CAPTION LOCATORS ================")
        print(f"  LOC_CAPTION_CANDIDATES = {C.LOC_CAPTION_CANDIDATES}")
        print("  (production _fill_caption tries each (role, name) via is_visible,")
        print("   then .fill(); _advance_to_caption clicks Next up to 3x FIRST to")
        print("   reveal the caption field — this probe does NOT click Next.)")

        # ── 4. Enumerate EVERY editable element in the current composer ─────
        #     tag / role / aria-label / placeholder / contenteditable / text /
        #     visibility / bounding box — the exact facts requested.
        EDITABLE_JS = r"""
        () => {
          const sel = "textarea, input, [contenteditable], [role=textbox]";
          const nodes = Array.from(document.querySelectorAll(sel));
          const out = [];
          for (const el of nodes) {
            const r = el.getBoundingClientRect();
            const cs = window.getComputedStyle(el);
            const visible = r.width>0 && r.height>0
              && cs.visibility !== 'hidden' && cs.display !== 'none';
            let oh = el.outerHTML || ''; if (oh.length > 240) oh = oh.slice(0,240)+'…';
            out.push({
              tag: el.tagName ? el.tagName.toLowerCase() : '',
              role: el.getAttribute('role'),
              ariaLabel: el.getAttribute('aria-label'),
              ariaPlaceholder: el.getAttribute('aria-placeholder'),
              placeholder: el.getAttribute('placeholder'),
              contenteditable: el.getAttribute('contenteditable'),
              type: el.getAttribute('type'),
              text: (el.innerText || el.value || '').trim().slice(0,60),
              visible: visible,
              box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
              outerHTML: oh,
            });
          }
          return out;
        }"""
        before_editables = await page.evaluate(EDITABLE_JS)
        print("\n================ EDITABLE ELEMENTS (post-attach, pre-Next) ================")
        print(f"  count = {len(before_editables)}")
        for i, d in enumerate(before_editables):
            print(f"  [{i}] tag={d['tag']} role={d['role']!r} visible={d['visible']}")
            print(f"       aria-label={d['ariaLabel']!r} aria-placeholder={d['ariaPlaceholder']!r} "
                  f"placeholder={d['placeholder']!r}")
            print(f"       contenteditable={d['contenteditable']!r} type={d['type']!r} text={d['text']!r}")
            print(f"       box={d['box']}")
            print(f"       outerHTML={d['outerHTML']!r}")

        # ── 5. Dry-run the production caption resolution (NO fill, NO click) ─
        #     For each candidate, run the SAME resolver production uses
        #     (is_visible -> _resolve_visible) and describe what it finds. We do
        #     NOT call .fill(), so nothing is typed and no DOM is mutated.
        print("\n================ PRODUCTION RESOLUTION DRY-RUN (no fill) ================")
        any_resolved = False
        for role, name in C.LOC_CAPTION_CANDIDATES:
            vis = False
            try:
                vis = await adapter.is_visible(role, name, timeout_s=C.PROBE_TIMEOUT)
            except Exception as ex:
                print(f"  ({role!r},{name!r}) is_visible RAISED {type(ex).__name__}: {str(ex)[:120]}")
                continue
            print(f"  ({role!r},{name!r}) is_visible={vis}")
            if vis:
                any_resolved = True
                try:
                    loc = await adapter._resolve_visible(role, name, timeout_s=C.PROBE_TIMEOUT)
                    if loc is not None:
                        d = await loc.evaluate(
                            "(e)=>({tag:e.tagName.toLowerCase(),role:e.getAttribute('role'),"
                            "ariaLabel:e.getAttribute('aria-label'),"
                            "contenteditable:e.getAttribute('contenteditable')})")
                        print(f"       resolved -> {d}")
                except Exception as ex:
                    print(f"       resolve-describe error {type(ex).__name__}")

        # ── 6. Did caption production step throw or just fail verification? ─
        #     Mirror _fill_caption's control flow in a dry run: it returns False
        #     when NO candidate is_visible (verification failure), and only
        #     raises inside .fill() (which we deliberately do NOT call). So:
        would_fill = any_resolved
        print("\n================ CAPTION-ENTRY FAILURE CLASSIFICATION ================")
        print(f"  any caption candidate visible (post-attach, pre-Next) = {any_resolved}")
        print(f"  production _fill_caption would {'ATTEMPT .fill()' if would_fill else 'return False (verification fail)'}"
              f" at THIS point")
        print("  NOTE: production reaches _fill_caption only AFTER _advance_to_caption")
        print("        clicks Next. If the candidates are absent here but the editable")
        print("        enumeration shows a caption field with a DIFFERENT aria-label,")
        print("        the fix is the locator; if the field only appears after Next,")
        print("        the fix is advancing. This probe clicks NO Next by request.")

        # ── 7. Screenshot for visual confirmation ───────────────────────────
        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")), "_diag")
        os.makedirs(shot_dir, exist_ok=True)
        shot = os.path.join(shot_dir, "instagram_caption_probe.png")
        try:
            await adapter.screenshot(shot); print(f"\n[probe-cap] screenshot: {shot}")
        except Exception as ex:
            print(f"\n[probe-cap] screenshot failed: {type(ex).__name__}")

        print("\n================ CAPTION DIAGNOSTIC SUMMARY ================")
        print(f"  composer opened:              {opened}")
        print(f"  media attached (_image_attached): {attached}")
        print(f"  caption candidates:           {C.LOC_CAPTION_CANDIDATES}")
        print(f"  any candidate visible now:    {any_resolved}")
        print(f"  editable elements present:    {len(before_editables)}")
        print("  (Paste the EDITABLE ELEMENTS dump so the real caption field's")
        print("   tag/role/aria-label/contenteditable can be matched.)")
        print("\n[probe-cap] No Next clicked, no Share clicked, no caption typed, nothing published.")
    finally:
        await adapter.close()
        print("[probe-cap] closed cleanly.")


# Shared JS: describe every editable element in the current DOM.
_EDITABLE_SWEEP_JS = r"""
() => {
  const sel = "textarea, input, [contenteditable], [role=textbox]";
  const nodes = Array.from(document.querySelectorAll(sel));
  const out = [];
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);
    const visible = r.width>0 && r.height>0
      && cs.visibility !== 'hidden' && cs.display !== 'none';
    let oh = el.outerHTML || ''; if (oh.length > 240) oh = oh.slice(0,240)+'…';
    out.push({
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      role: el.getAttribute('role'),
      ariaLabel: el.getAttribute('aria-label'),
      ariaPlaceholder: el.getAttribute('aria-placeholder'),
      placeholder: el.getAttribute('placeholder'),
      contenteditable: el.getAttribute('contenteditable'),
      type: el.getAttribute('type'),
      text: (el.innerText || el.value || '').trim().slice(0,60),
      visible: visible,
      box: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
      outerHTML: oh,
    });
  }
  return out;
}"""


async def cmd_probe_caption_after_next(args):
    """READ-MOSTLY diagnostic for the caption-entry failure, AFTER the Next
    transition. Opens the composer via the confirmed path, attaches the image
    exactly as production does, verifies _image_attached(), then calls the
    EXISTING production _advance_to_caption(browser) (which is allowed to click
    Next here — that is the transition under investigation). It STOPS right after
    _advance_to_caption() and inspects the resulting composer state.

    STRICT SAFETY (per request):
      * Allowed: clicking Next (ONLY via the unmodified production
        _advance_to_caption method).
      * Does NOT type/fill any caption.
      * Does NOT click Share.
      * Does NOT publish.

    Nothing in production is modified. If _advance_to_caption() does not surface
    the caption field, the probe diagnoses exactly why (Next visibility / click /
    UI change) using a PARALLEL read-only trace that mirrors the production loop
    for reporting purposes only — it never alters the production method.

    Requires --media (defaults to the known sigma logo path).
    """
    import os
    from app.social_publishing.providers.hermes.instagram.workflow import HermesInstagramWorkflow

    media = args.media or r"C:\Users\devan\Downloads\sigma_logo.png"
    pk = _profile_key(args.tenant, args.account)
    adapter = PlaywrightHermesAdapter(pk)
    await adapter.start()
    try:
        page = adapter._require_page()
        wf = HermesInstagramWorkflow()

        # ── 1. Host-side file sanity ────────────────────────────────────────
        exists = os.path.exists(media)
        isfile = os.path.isfile(media)
        size = os.path.getsize(media) if exists and isfile else None
        print(f"[probe-can] media path = {media}")
        print(f"[probe-can] exists={exists} isfile={isfile} size={size}")
        if not (exists and isfile):
            print("RESULT: MEDIA_FILE_NOT_FOUND"); return

        # ── 2. Open composer via the confirmed workflow method ──────────────
        await adapter.goto(C.HOME_URL, timeout_s=45)
        await asyncio.sleep(3)
        opened = await wf._open_composer(adapter)
        print(f"[probe-can] composer opened = {opened}")
        if not opened:
            print("RESULT: COMPOSER_DID_NOT_OPEN"); return
        await asyncio.sleep(2)

        # ── 3. Attach via the production selector/path ──────────────────────
        attach_err = None
        try:
            await page.locator(C.IMAGE_FILE_INPUT_SELECTOR).first.set_input_files(media)
        except Exception as e:
            attach_err = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-can] attach error = {attach_err}")
        await asyncio.sleep(3)

        attached = await wf._image_attached(adapter)
        print(f"[probe-can] _image_attached() = {attached}")
        if not attached:
            print("RESULT: IMAGE_NOT_ATTACHED (cannot advance to caption)"); return

        async def describe_next_candidates(tag):
            """Read-only: for each Next candidate, report whether production's
            resolver sees it visible, and describe the resolved element."""
            print(f"\n  --- Next candidates [{tag}] ---")
            seen = False
            for role, name in C.LOC_NEXT_CANDIDATES:
                vis = False
                try:
                    vis = await adapter.is_visible(role, name, timeout_s=C.PROBE_TIMEOUT)
                except Exception as ex:
                    print(f"    ({role!r},{name!r}) is_visible RAISED {type(ex).__name__}")
                    continue
                print(f"    ({role!r},{name!r}) is_visible={vis}")
                if vis and not seen:
                    seen = True
                    try:
                        loc = await adapter._resolve_visible(role, name, timeout_s=C.PROBE_TIMEOUT)
                        if loc is not None:
                            d = await loc.evaluate(
                                "(e)=>({tag:e.tagName.toLowerCase(),role:e.getAttribute('role'),"
                                "ariaLabel:e.getAttribute('aria-label'),text:(e.innerText||'').trim().slice(0,30),"
                                "disabled:e.getAttribute('aria-disabled')})")
                            print(f"       resolved Next -> {d}")
                    except Exception as ex:
                        print(f"       Next resolve-describe error {type(ex).__name__}")
            return seen

        async def caption_candidate_report(tag):
            print(f"\n  --- Caption candidates [{tag}] ---")
            resolved = None
            for role, name in C.LOC_CAPTION_CANDIDATES:
                vis = False
                try:
                    vis = await adapter.is_visible(role, name, timeout_s=C.PROBE_TIMEOUT)
                except Exception as ex:
                    print(f"    ({role!r},{name!r}) is_visible RAISED {type(ex).__name__}")
                    continue
                print(f"    ({role!r},{name!r}) is_visible={vis}")
                if vis and resolved is None:
                    resolved = (role, name)
            print(f"    => first resolving caption candidate = {resolved}")
            return resolved

        async def snapshot(tag):
            js = r"""
            () => {
              const vis = (sel) => Array.from(document.querySelectorAll(sel))
                 .filter(e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; }).length;
              return {
                url: location.href,
                dialogs: vis("[role=dialog]"),
                textboxes: vis("[role=textbox]"),
                contenteditable: vis("[contenteditable='true']"),
                textareas: vis("textarea"),
                bodyLen: document.body.innerHTML.length,
              };
            }"""
            return await page.evaluate(js)

        # ── State BEFORE _advance_to_caption ────────────────────────────────
        print("\n================ BEFORE _advance_to_caption ================")
        before = await snapshot("before")
        print(f"  url={before['url']}")
        print(f"  visible: dialogs={before['dialogs']} textboxes={before['textboxes']} "
              f"contenteditable={before['contenteditable']} textareas={before['textareas']} bodyLen={before['bodyLen']}")
        before_next_visible = await describe_next_candidates("before advance")
        await caption_candidate_report("before advance")

        # ── 4. Call the EXISTING production _advance_to_caption (allowed to
        #       click Next). We DO NOT modify it. It returns None by contract. ─
        print("\n[probe-can] calling production wf._advance_to_caption(adapter) …")
        advance_exc = None
        advance_ret = "<unset>"
        try:
            advance_ret = await wf._advance_to_caption(adapter)
        except Exception as e:
            advance_exc = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[probe-can] _advance_to_caption() returned = {advance_ret!r} (production method returns None by design)")
        print(f"[probe-can] _advance_to_caption() raised    = {advance_exc}")
        await asyncio.sleep(2)

        # ── 5. STOP. Inspect the post-advance state. ────────────────────────
        print("\n================ AFTER _advance_to_caption ================")
        after = await snapshot("after")
        print(f"  current URL = {after['url']}")
        print(f"  visible: dialogs={after['dialogs']} textboxes={after['textboxes']} "
              f"contenteditable={after['contenteditable']} textareas={after['textareas']} bodyLen={after['bodyLen']}")
        print(f"  UI changed by advance (bodyLen delta) = {after['bodyLen'] - before['bodyLen']}")

        # All editable elements, fully described.
        editables = await page.evaluate(_EDITABLE_SWEEP_JS)
        print(f"\n  EDITABLE ELEMENTS (post-advance) count = {len(editables)}")
        for i, d in enumerate(editables):
            print(f"  [{i}] tag={d['tag']} role={d['role']!r} visible={d['visible']}")
            print(f"       aria-label={d['ariaLabel']!r} aria-placeholder={d['ariaPlaceholder']!r} "
                  f"placeholder={d['placeholder']!r}")
            print(f"       contenteditable={d['contenteditable']!r} type={d['type']!r} text={d['text']!r}")
            print(f"       box={d['box']}")
            print(f"       outerHTML={d['outerHTML']!r}")

        # Production caption-candidate resolution, post-advance.
        resolved_caption = await caption_candidate_report("after advance")

        # ── 6. If no caption surfaced, diagnose the Next transition exactly ─
        caption_field_appeared = (
            resolved_caption is not None
            or after["textboxes"] > before["textboxes"]
            or after["contenteditable"] > before["contenteditable"]
            or after["textareas"] > before["textareas"]
        )
        if not caption_field_appeared:
            print("\n================ NEXT-TRANSITION DIAGNOSIS (no caption surfaced) ================")
            print(f"  Next visible BEFORE advance:   {before_next_visible}")
            after_next_visible = await describe_next_candidates("after advance")
            print(f"  Next visible AFTER advance:    {after_next_visible}")
            print(f"  URL changed:                   {before['url'] != after['url']}  "
                  f"({before['url']!r} -> {after['url']!r})")
            print(f"  bodyLen delta (UI changed):    {after['bodyLen'] - before['bodyLen']}")
            print("  Interpretation:")
            print("   - If Next was NOT visible before advance → _advance_to_caption's")
            print("     _first_visible(LOC_NEXT_CANDIDATES, click=True) found nothing to click,")
            print("     so it broke out immediately and the caption step was never reached.")
            print("   - If Next WAS visible and bodyLen changed but still no caption field →")
            print("     the composer advanced a step but the caption editor uses a role/name")
            print("     NOT in LOC_CAPTION_CANDIDATES (locator mismatch), OR more Next clicks")
            print("     are needed than the production loop's 3 iterations.")
            print("   - If Next WAS visible but bodyLen did NOT change → the click did not")
            print("     transition the composer (wrong Next element / overlay intercept).")

        # ── 7. Screenshot the post-Next state ───────────────────────────────
        shot_dir = os.path.join(
            os.getenv("HERMES_BROWSER_PROFILE_DIR",
                      os.path.join(os.getenv("TEMP", "."), "trendzzo_hermes_profiles")), "_diag")
        os.makedirs(shot_dir, exist_ok=True)
        shot = os.path.join(shot_dir, "instagram_caption_after_next.png")
        try:
            await adapter.screenshot(shot); print(f"\n[probe-can] screenshot: {shot}")
        except Exception as ex:
            print(f"\n[probe-can] screenshot failed: {type(ex).__name__}")

        print("\n================ CAPTION-AFTER-NEXT SUMMARY ================")
        print(f"  composer opened:               {opened}")
        print(f"  media attached:                {attached}")
        print(f"  _advance_to_caption() return:  {advance_ret!r} (None by production design)")
        print(f"  _advance_to_caption() raised:  {advance_exc}")
        print(f"  current URL:                   {after['url']}")
        print(f"  editable elements present:     {len(editables)}")
        print(f"  caption candidate resolved:    {resolved_caption}")
        print(f"  caption field appeared:        {caption_field_appeared}")
        print("  (Paste the EDITABLE ELEMENTS dump so the real caption field's")
        print("   tag/role/aria-label/contenteditable can be matched to a locator fix.)")
        print("\n[probe-can] STOPPED after _advance_to_caption. No caption typed, no Share, nothing published.")
    finally:
        await adapter.close()
        print("[probe-can] closed cleanly.")


async def cmd_prepare(args):
    import uuid
    from app.social_publishing.providers.hermes.instagram.service import InstagramUserAssistedService
    from app.social_publishing.providers.hermes.pending_confirmation_repository import (
        PendingConfirmationRepository,
    )

    operation_id = args.operation_id or f"instagram_smoke_{uuid.uuid4().hex[:12]}"
    print("[prepare] PREPARE-ONLY. This will NOT click Share. Expect ACTION_REQUIRED.")
    print(f"[prepare] tenant={args.tenant} account={args.account}")
    print(f"[prepare] operation_id={operation_id} media={[args.media]} caption={args.caption!r}")

    service = InstagramUserAssistedService()  # default factory => REAL adapter
    result = await service.prepare(
        tenant_id=args.tenant, account_id=args.account, operation_id=operation_id,
        caption=args.caption, media_paths=[args.media],
    )
    print("\n=== PREPARE RESULT (safe fields) ===")
    for k in ("status", "action", "message", "confirmation_id", "operation_id"):
        print(f"  {k:16} = {result.get(k)}")
    preview = result.get("preview") or {}
    if preview:
        print(f"  preview.caption     = {preview.get('caption')}")
        print(f"  preview.media_count = {preview.get('media_count')}")

    status = result.get("status")
    if status != "action_required":
        print(f"\n[prepare] status is '{status}', not 'action_required'. Nothing was published.")
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
            "platform == instagram": pending.platform == "instagram",
            "status == action_required": pending.status == "action_required",
            "provider_name == hermes": pending.provider_name == "hermes",
        }
        for label, ok in checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"  confirmation_id = {pending.id}")
    print(f"\nOPERATION_ID={operation_id}\nPREPARE_STATUS={status}")
    print("[prepare] STOPPED at ACTION_REQUIRED. Nothing was published.")


async def cmd_confirm(args):
    """Confirm + publish a previously-prepared Instagram post.

    Calls the EXISTING production confirmation path
    (InstagramUserAssistedService.confirm) — the SAME code the real API/app
    uses. There is NO second publish implementation here: this command only
    drives the service. It does not bypass the confirmation mechanism (it
    requires a real pending confirmation_id produced by `prepare`).

    THIS CLICKS SHARE AND PUBLISHES (via the production workflow) when a valid
    pending confirmation exists. Prints safe fields only — never cookies,
    tokens, or session contents.
    """
    from app.social_publishing.providers.hermes.instagram.service import (
        InstagramUserAssistedService,
    )
    from app.social_publishing.providers.hermes.pending_confirmation_repository import (
        PendingConfirmationRepository,
    )

    tenant = args.tenant
    confirmation_id = args.confirmation_id
    print("[confirm] Confirm + publish via production InstagramUserAssistedService.confirm().")
    print(f"[confirm] tenant={tenant} confirmation_id={confirmation_id}")

    # Read-only pre-fetch of the pending row for safe context fields the confirm
    # result does not itself return (operation_id / account_id). Never prints
    # secrets — PendingConfirmation holds no credentials/cookies/tokens.
    repo = PendingConfirmationRepository()
    pending = await repo.find(confirmation_id, tenant)
    operation_id = getattr(pending, "operation_id", None) if pending else None
    account_id = getattr(pending, "account_id", None) if pending else None
    if pending is None:
        print("[confirm] NOTE: no pending confirmation found for this id/tenant "
              "(service will report 'not_found').")

    service = InstagramUserAssistedService()  # default factory => REAL adapter
    result = await service.confirm(tenant_id=tenant, confirmation_id=confirmation_id)

    status = result.get("status")
    # Permalink / reason surface under different keys depending on outcome.
    permalink = result.get("external_url")
    external_post_id = result.get("external_post_id")
    reason = result.get("message") or result.get("error") or result.get("reason")
    platform = result.get("platform") or "instagram"
    provider = result.get("provider") or "hermes"

    print("\n=== CONFIRM RESULT (safe fields) ===")
    print(f"  status            = {status}")
    print(f"  operation_id      = {operation_id}")
    print(f"  platform          = {platform}")
    print(f"  provider          = {provider}")
    if account_id:
        print(f"  account_id        = {account_id}")
    if permalink:
        print(f"  permalink         = {permalink}")
    if external_post_id:
        print(f"  external_post_id  = {external_post_id}")
    if reason:
        print(f"  reason            = {reason}")

    # Clear success/failure verdict.
    print("\n=== PUBLISH VERDICT ===")
    if status == "published":
        print("  [SUCCESS] Instagram post PUBLISHED.")
        if permalink:
            print(f"            permalink: {permalink}")
    elif status == "unknown":
        print("  [UNKNOWN] Share was submitted but the result is uncertain — the")
        print("            post MAY exist. Verify manually; do NOT blind-retry.")
    elif status == "not_found":
        print("  [NOT PUBLISHED] No pending confirmation matched this id/tenant.")
    else:
        print(f"  [NOT PUBLISHED] status={status}. Nothing was published.")

    print(f"\nCONFIRM_STATUS={status}")


def main():
    p = argparse.ArgumentParser(description="Instagram Hermes smoke test (prepare-only)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("launch"); pl.add_argument("--tenant", default=""); pl.add_argument("--account", default="")
    pl.add_argument("--hold", type=int, default=300); pl.set_defaults(func=cmd_launch)

    ps = sub.add_parser("session"); ps.add_argument("--tenant", default=""); ps.add_argument("--account", default="")
    ps.add_argument("--hold", type=int, default=20); ps.set_defaults(func=cmd_session)

    ppost = sub.add_parser("probe-post"); ppost.add_argument("--tenant", required=True); ppost.add_argument("--account", required=True)
    ppost.set_defaults(func=cmd_probe_post)

    pcreate = sub.add_parser("probe-create"); pcreate.add_argument("--tenant", required=True); pcreate.add_argument("--account", required=True)
    pcreate.set_defaults(func=cmd_probe_create)

    pcc = sub.add_parser("probe-create-candidates"); pcc.add_argument("--tenant", required=True); pcc.add_argument("--account", required=True)
    pcc.set_defaults(func=cmd_probe_create_candidates)

    pnpc = sub.add_parser("probe-new-post-click"); pnpc.add_argument("--tenant", required=True); pnpc.add_argument("--account", required=True)
    pnpc.set_defaults(func=cmd_probe_new_post_click)

    pnpp = sub.add_parser("probe-new-post-parent"); pnpp.add_argument("--tenant", required=True); pnpp.add_argument("--account", required=True)
    pnpp.set_defaults(func=cmd_probe_new_post_parent)

    pnpa = sub.add_parser("probe-new-post-anchor"); pnpa.add_argument("--tenant", required=True); pnpa.add_argument("--account", required=True)
    pnpa.set_defaults(func=cmd_probe_new_post_anchor)

    pnpai = sub.add_parser("probe-new-post-anchor-inspect"); pnpai.add_argument("--tenant", required=True); pnpai.add_argument("--account", required=True)
    pnpai.set_defaults(func=cmd_probe_new_post_anchor_inspect)

    ppic = sub.add_parser("probe-post-item-click"); ppic.add_argument("--tenant", required=True); ppic.add_argument("--account", required=True)
    ppic.set_defaults(func=cmd_probe_post_item_click)

    pma = sub.add_parser("probe-media-attach"); pma.add_argument("--tenant", required=True); pma.add_argument("--account", required=True)
    pma.add_argument("--media", default=r"C:\Users\devan\Downloads\sigma_logo.png")
    pma.set_defaults(func=cmd_probe_media_attach)

    pcap = sub.add_parser("probe-caption"); pcap.add_argument("--tenant", required=True); pcap.add_argument("--account", required=True)
    pcap.add_argument("--media", default=r"C:\Users\devan\Downloads\sigma_logo.png")
    pcap.set_defaults(func=cmd_probe_caption)

    pcan = sub.add_parser("probe-caption-after-next"); pcan.add_argument("--tenant", required=True); pcan.add_argument("--account", required=True)
    pcan.add_argument("--media", default=r"C:\Users\devan\Downloads\sigma_logo.png")
    pcan.set_defaults(func=cmd_probe_caption_after_next)

    pb = sub.add_parser("probe"); pb.add_argument("--tenant", required=True); pb.add_argument("--account", required=True)
    pb.set_defaults(func=cmd_probe)

    pp = sub.add_parser("prepare"); pp.add_argument("--tenant", required=True); pp.add_argument("--account", required=True)
    pp.add_argument("--media", required=True); pp.add_argument("--caption", default="Trendzzo Hermes Instagram smoke test")
    pp.add_argument("--operation-id", dest="operation_id", default=""); pp.set_defaults(func=cmd_prepare)

    pcf = sub.add_parser("confirm"); pcf.add_argument("--tenant", required=True)
    pcf.add_argument("--confirmation-id", dest="confirmation_id", required=True)
    pcf.set_defaults(func=cmd_confirm)

    args = p.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
