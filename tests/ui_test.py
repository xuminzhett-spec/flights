"""Browser test for the web page (needs: python playwright + chromium). Run: python3 tests/ui_test.py"""
import base64
import json
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp())
OUT.mkdir(parents=True, exist_ok=True)
site = Path(tempfile.mkdtemp()) / "docs"
shutil.copytree(ROOT / "docs", site)

now = datetime.now(timezone.utc)
def t(days_ago): return (now - timedelta(days=days_ago)).replace(microsecond=0).isoformat()

routes = {"settings": {"currency": "CAD", "daily_search_limit": 3, "passport": "CN"}, "routes": [
    {"id": "a", "label": "回国过年", "from": "YEG", "to": "PVG", "depart": "2027-01-20", "return": "2027-02-20", "alert_below": 1300, "enabled": True},
    {"id": "b", "label": "", "from": "YEG", "to": "BKK", "depart": "2026-12-20", "return": None, "alert_below": 700, "enabled": True},
    {"id": "c", "label": "东京", "from": "YVR", "to": "NRT", "depart": "2026-12-10", "return": "2026-12-18", "alert_below": None, "enabled": True},
    {"id": "d", "label": "新路线", "from": "YEG", "to": "YVR", "depart": "2026-12-01", "return": None, "alert_below": None, "enabled": True},
]}
def hist(vals, bag):
    return [{"t": t(len(vals) - i), "min": v, "min_bag": (v + 120) if bag else None, "airline": "Air Canada", "stops": 1} for i, v in enumerate(vals)]
def o(p, al, st, bag, m=900): return {"price": p, "airline": al, "stops": st, "minutes": m, "bag": bag}
prices = {"updated": t(0.2), "currency": "CAD", "routes": {
    "a": {"meta": {"last_checked": t(0.2), "visa": {"country": "CN", "code": "home", "text": "本国"}},
          "history": hist([1620, 1580, 1490, 1510, 1420, 1380, 1355, 1290, 1340], True),
          "latest": [o(1340, "Air China", 1, "unknown", 1100), o(1460, "Air Canada", 0, "yes", 840), o(1520, "China Eastern", 1, "no", 1150)]},
    "b": {"meta": {"last_checked": t(1), "visa": {"country": "TH", "code": "free", "text": "免签（30天）"}},
          "history": hist([880, 860, 905, 840], False),
          "latest": [o(840, "Air Canada", 1, "unknown"), o(990, "EVA Air", 1, "unknown")]},
    "c": {"meta": {"last_checked": t(1), "visa": {"country": "JP", "code": "evisa", "text": "电子签证（需提前申请）"}},
          "history": hist([1100, 1090], True),
          "latest": [o(1090, "ANA", 0, "yes", 620)]},
}}
(site / "routes.json").write_text(json.dumps(routes, ensure_ascii=False))
(site / "data" / "prices.json").write_text(json.dumps(prices, ensure_ascii=False))

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(site)))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}/"

failures = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: failures.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="zh-CN")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    # ---- fake GitHub API
    gh = {"put": None, "dispatch": None, "doc": json.dumps(routes)}
    def gh_handler(route):
        req = route.request
        url, method = req.url, req.method
        cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"}
        if method == "OPTIONS":
            return route.fulfill(status=204, headers=cors)
        if url.endswith("/contents/docs/routes.json") and method == "GET":
            return route.fulfill(status=200, headers=cors, content_type="application/json", body=json.dumps({"sha": "abc123"}))
        if url.endswith("/contents/docs/routes.json") and method == "PUT":
            gh["put"] = json.loads(req.post_data)
            return route.fulfill(status=200, headers=cors, content_type="application/json", body="{}")
        if url.endswith("/dispatches"):
            gh["dispatch"] = json.loads(req.post_data)
            return route.fulfill(status=204, headers=cors)
        if url.endswith("/repos/me/flights"):
            return route.fulfill(status=200, headers=cors, content_type="application/json", body=json.dumps({"default_branch": "main"}))
        route.fulfill(status=404, headers=cors, body="{}")
    ctx.route("https://api.github.com/**", gh_handler)

    page.goto(BASE)
    page.wait_for_selector(".card")
    check(page.locator(".card").count() == 4, "4 route cards rendered")
    check("CAD 1,340" in page.locator(".card").nth(0).inner_text(), "card A shows cheapest CAD 1,340")
    check("▲ 50" in page.locator(".card").nth(0).inner_text(), "card A shows price rise vs previous check (1290 -> 1340)")
    check("▼" in page.locator(".card").nth(1).inner_text(), "card B shows price drop (905 -> 840)")
    check("等待首次检查" in page.locator(".card").nth(3).inner_text(), "card D waiting for first check")
    check("免签（30天）" in page.locator(".card").nth(1).inner_text(), "visa badge on Thailand")
    page.screenshot(path=str(OUT / "1-list.png"))

    # open details
    page.locator(".card-top").nth(0).click()
    check(page.locator("svg.chart").count() == 1, "chart drawn on open")
    check(page.locator("table tr").count() == 4, "options table has 3 rows + header")
    page.screenshot(path=str(OUT / "2-detail.png"), full_page=True)

    # filters
    page.check("#f-bag")
    page.wait_for_timeout(100)
    cards = page.locator(".card")
    check(cards.count() == 3, "bag filter hides route B (no checked-bag price), keeps A, C and not-yet-checked D")
    check("CAD 1,460" in cards.nth(0).inner_text(), "bag filter shows cheapest bag-included price")
    page.uncheck("#f-bag")
    page.check("#f-visa")
    page.wait_for_timeout(100)
    texts = " ".join(page.locator(".card").all_inner_texts())
    check("东京" not in texts and "泰国" not in texts and "BKK" in texts, "visa filter hides e-visa route, keeps visa-free")
    page.uncheck("#f-visa")
    page.check("#f-direct")
    page.wait_for_timeout(100)
    check(page.locator(".card").count() == 3, "direct filter hides route B (no nonstop), keeps A, C and D")
    page.uncheck("#f-direct")

    # no token -> add route opens settings
    page.click("#btn-add")
    page.fill("[name=from]", "yeg"); page.fill("[name=to]", "pek")
    page.fill("[name=depart]", "2027-03-01"); page.fill("[name=alert_below]", "1200")
    page.screenshot(path=str(OUT / "3-add.png"))
    page.click("#btn-save-route")
    page.wait_for_timeout(300)
    check(page.locator("#dlg-settings").evaluate("d => d.open"), "no token -> settings dialog opens")
    check(gh["put"] is None, "nothing saved without a token")
    page.screenshot(path=str(OUT / "4-settings.png"))
    page.fill("#form-settings [name=token]", "github_pat_test")
    page.fill("#form-settings [name=repo]", "me/flights")
    page.click("#form-settings button[type=submit]")
    page.wait_for_timeout(300)
    check(not page.locator("#dlg-settings").evaluate("d => d.open"), "settings saved & closed")

    # save route now
    if page.locator("#dlg-route").evaluate("d => d.open"):
        page.click("#btn-save-route")
    else:
        page.click("#btn-add")
        page.fill("[name=from]", "yeg"); page.fill("[name=to]", "pek")
        page.fill("[name=depart]", "2027-03-01"); page.fill("[name=alert_below]", "1200")
        page.click("#btn-save-route")
    page.wait_for_timeout(500)
    check(gh["put"] is not None and gh["put"].get("sha") == "abc123", "PUT sent with current sha")
    saved = json.loads(base64.b64decode(gh["put"]["content"]).decode()) if gh["put"] else {"routes": []}
    new = saved["routes"][-1] if saved["routes"] else {}
    check(new.get("from") == "YEG" and new.get("to") == "PEK" and new.get("alert_below") == 1200, "saved route has uppercase codes and target")
    check(len(saved["routes"]) == 5, "saved file contains old + new route")
    check(page.locator(".card").count() == 5, "list shows 5 cards after save")

    # delete
    page.locator(".card-top").nth(1).click()
    page.click("button[data-act=delete]")
    page.wait_for_selector("#dlg-confirm[open]")
    page.screenshot(path=str(OUT / "5-confirm.png"))
    page.click("#confirm-ok")
    page.wait_for_timeout(500)
    saved = json.loads(base64.b64decode(gh["put"]["content"]).decode())
    check(len(saved["routes"]) == 4, "delete saved (4 routes left)")

    # run now
    page.click("#btn-run")
    page.wait_for_timeout(500)
    check(gh["dispatch"] == {"ref": "main"}, "run-now dispatches workflow on default branch")

    check(not errors, f"no console errors {errors}")

    # dark mode + empty state
    ctx2 = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark", locale="zh-CN")
    pg2 = ctx2.new_page()
    pg2.goto(BASE); pg2.wait_for_selector(".card"); pg2.locator(".card-top").nth(1).click()
    pg2.screenshot(path=str(OUT / "6-dark.png"), full_page=True)
    b.close()

srv.shutdown()
print("\nFAILURES:" if failures else "\nALL UI CHECKS PASSED", failures or "")
sys.exit(1 if failures else 0)
