#!/usr/bin/env python3
"""Alert when CIPLE exam centers are listed for the United States on CAPLE."""

import base64
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

# Persists the last-known list of centers so we can detect changes between runs
STATE_FILE = Path("state.json")
REGISTRATION_URL = "https://caple.letras.ulisboa.pt/inscricao"
# API endpoint that returns exam centers filtered by country and exam type
CENTERS_URL = (
    "https://caple.letras.ulisboa.pt/centers/getCentersExamsByCountry.json"
    "?country_id={country_id}&exam_id={exam_id}"
)
SEASONS_URL = (
    "https://caple.letras.ulisboa.pt/seasons/getSeasonsByCenterExam.json"
    "?exam_id={exam_id}&center_id={center_id}"
)

CIPLE_EXAM_ID = "2"

COUNTRIES = {
    "69": "United States",
    "193": "Portugal",
}


def load_state():
    # Returns the saved state, or a blank default if no state file exists yet
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        # Migrate legacy flat format to per-country format
        if "centers" in data and not isinstance(data["centers"], dict):
            return {"countries": {}, "last_checked": None}
        return data
    return {"countries": {}, "last_checked": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def fetch_centers(country_id: str, exam_id: str) -> list[dict]:
    url = CENTERS_URL.format(country_id=country_id, exam_id=exam_id)
    # Spoof a browser User-Agent and set Referer so the API doesn't reject the request
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": REGISTRATION_URL,
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    # Extract just city and name from each center entry; skip empty records
    centers = []
    for item in data.get("centers", []):
        c = item.get("Center", {})
        center_id = str(c.get("id") or "").strip()
        city = str(c.get("city") or "").strip()
        name = str(c.get("name") or "").strip()
        if city or name:
            centers.append({"id": center_id, "city": city, "name": name})
    return centers


def fetch_seasons(center_id: str, exam_id: str) -> list[dict]:
    url = SEASONS_URL.format(exam_id=exam_id, center_id=center_id)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": REGISTRATION_URL,
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    seasons = []
    for item in data.get("seasons", []):
        s = item.get("Season", item)
        entry = {}
        for field in ("id", "name", "date_ciple"):
            if s.get(field):
                entry[field] = str(s[field]).strip()
        if entry:
            seasons.append(entry)
    return seasons


def send_email(subject, body):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    # Falls back to sending to the sender's own address if NOTIFY_EMAIL isn't set
    notify_email = os.environ.get("NOTIFY_EMAIL", gmail_user)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(gmail_user, gmail_password)
        _send_message(smtp, gmail_user, notify_email, subject, body)
    print(f"Email sent to {notify_email}")

    if os.environ.get("NOTIFY_SMS"):
        send_sms(f"{subject}\n{REGISTRATION_URL}")


def send_sms(body):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_FROM_NUMBER"]
    to_number = os.environ["NOTIFY_SMS"]

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = urllib.parse.urlencode(
        {"From": from_number, "To": to_number, "Body": body}
    ).encode("utf-8")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    print(f"SMS sent to {to_number} via Twilio")


def _send_message(smtp, from_addr, to_addr, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    smtp.send_message(msg)


def format_centers(centers):
    # Returns a human-readable bullet list of centers for use in the email body
    if not centers:
        return "  (none found)"
    lines = []
    for c in centers:
        city = c.get("city", "").strip()
        name = c.get("name", "").strip()
        lines.append(f"  - {city} — {name}" if city and name else f"  - {city or name or 'Unknown'}")
    return "\n".join(lines)


def check_country(country_id, country_name, state, force_notify, now):
    prev_centers = state.get("countries", {}).get(country_id, [])

    try:
        centers = fetch_centers(country_id, CIPLE_EXAM_ID)
    except Exception as e:
        print(f"ERROR: Fetch failed for {country_name}: {e}")
        return False

    print(f"{country_name} — Centers found: {len(centers)}")
    for c in centers:
        print(f"  {c['city']} — {c['name']}")

    # Strip seasons before comparing so date changes don't trigger notifications
    prev_stripped = [{k: v for k, v in c.items() if k != "seasons"} for c in prev_centers]
    prev_json = json.dumps(prev_stripped, sort_keys=True)
    curr_json = json.dumps(centers, sort_keys=True)
    listings_changed = prev_json != curr_json

    # Enrich each center with its exam season dates
    for c in centers:
        if c.get("id"):
            try:
                c["seasons"] = fetch_seasons(c["id"], CIPLE_EXAM_ID)
                print(f"  Seasons for {c['city']}: {len(c['seasons'])} found")
            except Exception as e:
                print(f"  WARNING: Could not fetch seasons for {c.get('city', c['id'])}: {e}")
                c["seasons"] = []

    state.setdefault("countries", {})[country_id] = centers

    should_notify = (listings_changed and centers) or force_notify

    if not should_notify:
        print(f"{country_name} — No changes, no notification sent.")
        return True

    if force_notify and not listings_changed:
        # Test email: confirms the workflow is running but no real change occurred
        subject = f"[TEST] CIPLE Alert — Checker is Running ({country_name})"
        body = (
            f"Test notification from the CIPLE exam date checker.\n\n"
            f"Country: {country_name}\n\n"
            f"Status: {len(centers)} exam center(s) currently listed:\n\n"
            f"{format_centers(centers)}\n\n"
            f"Registration: {REGISTRATION_URL}\n\n"
            f"Checked: {now}"
        ) if centers else (
            f"Test notification from the CIPLE exam date checker.\n\n"
            f"Country: {country_name}\n\n"
            f"Status: No exam listings found yet.\n\n"
            f"The checker is running correctly. You will be notified\n"
            f"as soon as exam centers are listed for {country_name}.\n\n"
            f"Checked: {now}"
        )
    else:
        # Real alert: centers have appeared for the first time (or changed)
        subject = f"CIPLE Exam Listings Now Available in {country_name}"
        body = (
            f"CIPLE exam center listings have appeared for {country_name}.\n\n"
            f"Centers found ({len(centers)}):\n\n"
            f"{format_centers(centers)}\n\n"
            f"Register here: {REGISTRATION_URL}\n\n"
            f"Checked: {now}"
        )

    send_email(subject, body)
    return True


def main():
    force_notify = os.environ.get("FORCE_NOTIFY", "").lower() in ("true", "1", "yes")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"=== CIPLE Date Checker — {now} ===")
    if force_notify:
        print("Force notify mode: ON")

    state = load_state()
    any_error = False

    for country_id, country_name in COUNTRIES.items():
        success = check_country(country_id, country_name, state, force_notify, now)
        if not success:
            any_error = True

    state["last_checked"] = now
    save_state(state)

    if any_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
