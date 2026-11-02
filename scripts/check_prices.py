#!/usr/bin/env python3
"""
Daily flight price checker. Runs inside GitHub Actions (stdlib only, no pip install).

Reads   docs/routes.json            (edited from the web app)
Writes  docs/data/prices.json       (price history + latest options)
        docs/data/reference.json    (airport -> country, visa rules for your passport)
Sends   one summary e-mail when a route drops below its target price.

Environment variables (GitHub repo "Secrets"):
  SERPAPI_KEY   SerpApi key (Google Flights data)
  SMTP_USER     Gmail address used to SEND the alert
  SMTP_PASS     Gmail "app password" (NOT your normal password)
  ALERT_TO      where alerts are delivered (optional, defaults to SMTP_USER)
"""
import csv
import io
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ROUTES_FILE = DOCS / "routes.json"
PRICES_FILE = DOCS / "data" / "prices.json"
REF_FILE = DOCS / "data" / "reference.json"

AIRPORTS_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
PASSPORT_URL = "https://raw.githubusercontent.com/ilyankou/passport-index-dataset/master/passport-index-tidy-iso2.csv"

MAX_HISTORY = 400
VISA_REFRESH_DAYS = 30
ALERT_REPEAT_DAYS = 7

VISA_OK_CODES = {"free", "voa", "home"}  # no need to apply before travelling


# ----------------------------------------------------------------- helpers
def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def http_get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "flight-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ----------------------------------------------------------------- visa / airports
def classify_requirement(value):
    """Map a passport-index 'Requirement' cell to (code, Chinese text)."""
    s = str(value).strip().lower()
    if re.fullmatch(r"\d+", s):
        return "free", f"免签（{s}天）"
    if s == "visa free":
        return "free", "免签"
    if s == "visa on arrival":
        return "voa", "落地签"
    if s in ("e-visa", "evisa"):
        return "evisa", "电子签证（需提前申请）"
    if s == "eta":
        return "eta", "电子旅行许可 ETA（需提前申请）"
    if s == "visa required":
        return "required", "需提前办签证"
    if s in ("no admission", "covid ban"):
        return "banned", "暂不允许入境"
    if s in ("-1", "self"):
        return "home", "本国"
    return "unknown", "未知"


def build_visa_map(csv_text, passport):
    out = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        if (row.get("Passport") or "").strip().upper() != passport:
            continue
        dest = (row.get("Destination") or "").strip().upper()
        req = row.get("Requirement")
        if dest and req is not None:
            code, text = classify_requirement(req)
            out[dest] = {"code": code, "text": text}
    return out


def resolve_airports(csv_text, wanted):
    found = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        iata = (row.get("iata_code") or "").strip().upper()
        if iata in wanted and iata not in found and row.get("iso_country"):
            found[iata] = row["iso_country"].strip().upper()
    return found


def ensure_reference(ref, codes, passport, download=http_get):
    """Fill in airport countries and the visa table. Never raises: failures are logged."""
    ref.setdefault("airports", {})
    ref.setdefault("visa", {})

    # visa table (refresh monthly or when the passport changed)
    stale = True
    if ref.get("visa") and ref.get("passport") == passport and ref.get("visa_updated"):
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(ref["visa_updated"])
            stale = age > timedelta(days=VISA_REFRESH_DAYS)
        except ValueError:
            stale = True
    if stale:
        try:
            text = download(PASSPORT_URL).decode("utf-8-sig")
            visa = build_visa_map(text, passport)
            if visa:
                ref["visa"], ref["passport"], ref["visa_updated"] = visa, passport, now_iso()
                print(f"[ref] visa table updated for {passport}: {len(visa)} destinations")
            else:
                print("[ref] WARNING: visa dataset had no rows for this passport")
        except Exception as e:  # noqa: BLE001
            print(f"[ref] WARNING: could not download visa data: {e}")

    # airports we do not know yet
    missing = {c for c in codes if c not in ref["airports"]}
    if missing:
        try:
            text = download(AIRPORTS_URL).decode("utf-8-sig")
            found = resolve_airports(text, missing)
            ref["airports"].update(found)
            print(f"[ref] resolved airports: {found}")
            for c in missing - set(found):
                print(f"[ref] WARNING: unknown airport code {c}")
        except Exception as e:  # noqa: BLE001
            print(f"[ref] WARNING: could not download airport data: {e}")
    return ref


def visa_for(ref, dest_code):
    country = ref.get("airports", {}).get(dest_code)
    if not country:
        return {"country": None, "code": "unknown", "text": "未知"}
    info = ref.get("visa", {}).get(country)
    if not info:
        if country == ref.get("passport"):
            return {"country": country, "code": "home", "text": "本国"}
        return {"country": country, "code": "unknown", "text": "未知"}
    return {"country": country, **info}


# ----------------------------------------------------------------- flights
def bag_status(option):
    """Best-effort guess from SerpApi text. Returns 'yes', 'no' or 'unknown'."""
    texts = list(option.get("extensions") or [])
    for leg in option.get("flights") or []:
        texts += leg.get("extensions") or []
    t = " | ".join(str(x) for x in texts).lower()
    if not t:
        return "unknown"
    if re.search(r"(no|without|not include\w*) checked bag|checked bag\w* (for a fee|extra|not included)|"
                 r"checked baggage for a fee|bag fees?", t):
        return "no"
    if re.search(r"checked bag\w* (included|free)|free checked bag|1st checked bag free|"
                 r"\d checked bags? included", t):
        return "yes"
    return "unknown"


def google_flights_link(route):
    q = f"Flights from {route['from']} to {route['to']} on {route['depart']}"
    if route.get("return"):
        q += f" through {route['return']}"
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"q": q})


def fetch_flights(route, settings, api_key):
    params = {
        "engine": "google_flights",
        "departure_id": route["from"],
        "arrival_id": route["to"],
        "outbound_date": route["depart"],
        "currency": settings.get("currency", "CAD"),
        "hl": "en",
        "api_key": api_key,
        "type": 1 if route.get("return") else 2,
    }
    if route.get("return"):
        params["return_date"] = route["return"]
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    data = json.loads(http_get(url, timeout=90))
    if data.get("error"):
        raise RuntimeError(data["error"])
    return parse_options(data)


def parse_options(data):
    flights = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    out = []
    for f in flights:
        if "price" not in f:
            continue
        legs = f.get("flights") or []
        out.append({
            "price": f["price"],
            "airline": ", ".join(sorted({leg.get("airline", "?") for leg in legs})) or "?",
            "stops": max(len(legs) - 1, 0),
            "minutes": f.get("total_duration"),
            "bag": bag_status(f),
        })
    out.sort(key=lambda o: o["price"])
    return out


# ----------------------------------------------------------------- core
def is_active(route, today):
    if route.get("enabled") is False:
        return False
    try:
        return date.fromisoformat(route["depart"]) >= today
    except (KeyError, ValueError):
        return False


def record_result(prices, route, options, visa):
    rec = prices.setdefault("routes", {}).setdefault(route["id"], {"meta": {}, "history": [], "latest": []})
    cheapest = options[0]
    bag_prices = [o["price"] for o in options if o["bag"] == "yes"]
    rec["history"].append({
        "t": now_iso(),
        "min": cheapest["price"],
        "min_bag": min(bag_prices) if bag_prices else None,
        "airline": cheapest["airline"],
        "stops": cheapest["stops"],
    })
    rec["history"] = rec["history"][-MAX_HISTORY:]
    rec["latest"] = options[:10]
    rec["meta"].update({"last_checked": now_iso(), "visa": visa, "label": route.get("label") or ""})
    rec["meta"].pop("last_error", None)
    return rec


def alert_candidate(route, rec):
    """Return the price that should be compared with the target, or None."""
    visa_code = (rec["meta"].get("visa") or {}).get("code", "unknown")
    if route.get("visa_ok_only") and visa_code not in VISA_OK_CODES:
        return None
    options = rec.get("latest") or []
    if route.get("need_checked_bag"):
        options = [o for o in options if o["bag"] == "yes"]
    return min((o["price"] for o in options), default=None)


def should_alert(route, rec, price):
    target = route.get("alert_below")
    if not target or price is None or price > float(target):
        return False
    last = rec["meta"].get("last_alert_price")
    last_at = rec["meta"].get("last_alert_at")
    if last is None or price < last:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(last_at) > timedelta(days=ALERT_REPEAT_DAYS)
    except (TypeError, ValueError):
        return True


def build_email(alerts, currency):
    lines = ["以下航线的价格已低于你的目标价：", ""]
    for route, rec, price in alerts:
        name = route.get("label") or f"{route['from']}→{route['to']}"
        visa = (rec["meta"].get("visa") or {}).get("text", "未知")
        trip = f"{route['depart']}" + (f" ~ {route['return']}" if route.get("return") else "（单程）")
        lines += [
            f"■ {name}  {route['from']}→{route['to']}  {trip}",
            f"  当前最低：{currency} {price}（目标 {route['alert_below']}）",
            f"  签证（按你的护照）：{visa}",
            f"  查看：{google_flights_link(route)}",
            "",
        ]
    lines.append("提示：价格随时会变，下单前请在航空公司或订票网站再确认一次，并核对行李规定。")
    return "\n".join(lines)


def send_email(body, subject):
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    to = os.environ.get("ALERT_TO") or user
    if not (user and pw and to):
        print("[mail] SMTP_USER / SMTP_PASS not set; skipping e-mail")
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"[mail] sent to {to}")
    return True


def run(fetch=fetch_flights, download=http_get, today=None):
    today = today or date.today()
    routes_doc = load_json(ROUTES_FILE, {"settings": {}, "routes": []})
    settings = {"currency": "CAD", "daily_search_limit": 3, "passport": "CN", **routes_doc.get("settings", {})}
    routes = [r for r in routes_doc.get("routes", []) if is_active(r, today)]
    for r in routes:
        r["from"], r["to"] = r["from"].upper(), r["to"].upper()
    prices = load_json(PRICES_FILE, {"routes": {}})
    ref = load_json(REF_FILE, {})

    api_key = os.environ.get("SERPAPI_KEY", "")
    if not api_key:
        print("ERROR: SERPAPI_KEY is not set (add it in the repo's Secrets).")
        return 1

    # drop history of routes that were deleted from routes.json
    known = {r["id"] for r in routes_doc.get("routes", [])}
    prices["routes"] = {k: v for k, v in prices.get("routes", {}).items() if k in known}

    limit = int(settings.get("daily_search_limit") or 3)
    if os.environ.get("CHECK_ALL") == "1":
        limit = len(routes)
    routes.sort(key=lambda r: prices["routes"].get(r["id"], {}).get("meta", {}).get("last_checked") or "")
    batch = routes[:limit]
    print(f"{len(routes)} active route(s); checking {len(batch)} today (limit {limit}).")

    codes = {c for r in routes for c in (r["from"], r["to"])}
    ensure_reference(ref, codes, settings["passport"], download)

    alerts, failures = [], 0
    for r in batch:
        label = f"{r['from']}->{r['to']} {r['depart']}"
        try:
            options = fetch(r, settings, api_key)
            if not options:
                print(f"[{label}] no prices returned")
                continue
            rec = record_result(prices, r, options, visa_for(ref, r["to"]))
            price = alert_candidate(r, rec)
            print(f"[{label}] cheapest {options[0]['price']}  (alert basis: {price})")
            if should_alert(r, rec, price):
                alerts.append((r, rec, price))
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"[{label}] FAILED: {e}")
            rec = prices["routes"].setdefault(r["id"], {"meta": {}, "history": [], "latest": []})
            rec["meta"]["last_error"] = str(e)[:200]

    prices["updated"] = now_iso()
    prices["currency"] = settings["currency"]
    if alerts:
        try:
            body = build_email(alerts, settings["currency"])
            if send_email(body, f"✈ 机票降价提醒（{len(alerts)} 条航线）"):
                for r, rec, price in alerts:
                    rec["meta"]["last_alert_price"], rec["meta"]["last_alert_at"] = price, now_iso()
        except Exception as e:  # noqa: BLE001
            print(f"[mail] FAILED: {e}")
    save_json(PRICES_FILE, prices)
    save_json(REF_FILE, ref)
    return 1 if (batch and failures == len(batch)) else 0


if __name__ == "__main__":
    sys.exit(run())
