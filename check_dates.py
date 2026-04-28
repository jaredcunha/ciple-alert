#!/usr/bin/env python3
"""Check CAPLE registration site for CIPLE exam listings in the United States."""

import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

STATE_FILE = Path("state.json")
DATA_URL = "https://caple.letras.ulisboa.pt/inscricao.json"
REGISTRATION_URL = "https://caple.letras.ulisboa.pt/inscricao"

# Dropdown option value for the United States in the country select.
US_COUNTRY_VALUE = "69"

# CIPLE exam id in the exams array.
CIPLE_EXAM_ID = "2"


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"centers": [], "last_checked": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def fetch_inscricao_json() -> dict:
    req = urllib.request.Request(
        DATA_URL,
        headers={"Accept": "application/json", "Referer": REGISTRATION_URL},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def scrape_us_listings() -> list[dict]:
    data = fetch_inscricao_json()

    countries = data.get("countries", [])
    print(f"inscricao.json: {len(countries)} countries, keys={list(data.keys())}")

    # ---- Find the target country ----
    # The dropdown <option> values may be array indices OR Country.id values.
    # Try both so we can see what matches.
    by_index = countries[int(US_COUNTRY_VALUE)] if US_COUNTRY_VALUE.isdigit() and int(US_COUNTRY_VALUE) < len(countries) else None
    by_id = next((c for c in countries if str(c.get("Country", c).get("id", "")) == US_COUNTRY_VALUE), None)

    print(f"By index [{US_COUNTRY_VALUE}]: {json.dumps(by_index, ensure_ascii=False)[:120] if by_index else 'out of range'}")
    print(f"By id=={US_COUNTRY_VALUE}:    {json.dumps(by_id, ensure_ascii=False)[:120] if by_id else 'not found'}")

    target = by_id or by_index
    if target is None:
        print("Target country not found. First 5 countries:")
        for i, c in enumerate(countries[:5]):
            co = c.get("Country", c)
            print(f"  [{i}] id={co.get('id','?')} name={co.get('name','?')}")
        return []

    print(f"\nTarget country full structure:\n{json.dumps(target, indent=2, ensure_ascii=False)[:1500]}\n")

    # ---- Extract centers / lapes ----
    centers_raw = (
        target.get("Centers")
        or target.get("Lapes")
        or target.get("lapes")
        or target.get("centers")
        or []
    )
    print(f"Centers/lapes found for country: {len(centers_raw)}")

    centers = []
    for item in centers_raw:
        center_obj = item.get("Center") or item.get("Lape") or item
        if not isinstance(center_obj, dict):
            continue

        # Only include centers that offer CIPLE (exam id=2).
        # If no exam list exists on the item, include it anyway.
        exam_ids = {
            str(e.get("exam_id") or e.get("Exam", {}).get("id", ""))
            for e in item.get("Exams", item.get("ExamLapes", item.get("ExamCenter", [])))
        }
        if exam_ids and CIPLE_EXAM_ID not in exam_ids:
            continue

        city = str(center_obj.get("city") or center_obj.get("City") or "").strip()
        name = str(center_obj.get("name") or center_obj.get("Name") or "").strip()
        if city or name:
            centers.append({"city": city, "name": name})

    return centers


def send_email(subject, body):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    notify_email = os.environ.get("NOTIFY_EMAIL", gmail_user)

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = notify_email

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(gmail_user, gmail_password)
        smtp.send_message(msg)

    print(f"Email sent to {notify_email}")


def format_centers(centers):
    if not centers:
        return "  (none found)"
    lines = []
    for c in centers:
        city = c.get("city", "").strip()
        name = c.get("name", "").strip()
        lines.append(f"  - {city} — {name}" if city and name else f"  - {city or name or 'Unknown'}")
    return "\n".join(lines)


def main():
    force_notify = os.environ.get("FORCE_NOTIFY", "").lower() in ("true", "1", "yes")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"=== CIPLE US Date Checker — {now} ===")
    if force_notify:
        print("Force notify mode: ON")

    state = load_state()
    prev_centers = state.get("centers", [])

    try:
        centers = scrape_us_listings()
    except Exception as e:
        print(f"ERROR: Scrape failed: {e}")
        sys.exit(1)

    print(f"\nPrevious center count: {len(prev_centers)}")
    print(f"Current center count:  {len(centers)}")

    prev_json = json.dumps(prev_centers, sort_keys=True)
    curr_json = json.dumps(centers, sort_keys=True)
    listings_changed = prev_json != curr_json
    print(f"Listings changed: {listings_changed}")

    state["centers"] = centers
    state["last_checked"] = now
    save_state(state)
    print(f"State saved to {STATE_FILE}")

    should_notify = (listings_changed and centers) or force_notify

    if not should_notify:
        print("No notification sent.")
        return

    if force_notify and not listings_changed:
        subject = "[TEST] CIPLE Alert — Checker is Running"
        body = (
            f"Test notification from the CIPLE exam date checker.\n\n"
            f"Status: {len(centers)} US exam center(s) currently listed:\n\n"
            f"{format_centers(centers)}\n\n"
            f"Registration: {REGISTRATION_URL}\n\n"
            f"Checked: {now}"
        ) if centers else (
            f"Test notification from the CIPLE exam date checker.\n\n"
            f"Status: No US exam listings found yet.\n\n"
            f"The checker is running correctly. You will be notified\n"
            f"as soon as exam centers are listed for the United States.\n\n"
            f"Checked: {now}"
        )
    else:
        subject = "CIPLE Exam Listings Now Available in the United States"
        body = (
            f"CIPLE exam center listings have appeared for the United States.\n\n"
            f"Centers found ({len(centers)}):\n\n"
            f"{format_centers(centers)}\n\n"
            f"Register here: {REGISTRATION_URL}\n\n"
            f"Checked: {now}"
        )

    send_email(subject, body)


if __name__ == "__main__":
    main()
