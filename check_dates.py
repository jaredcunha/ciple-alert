#!/usr/bin/env python3
"""Alert when CIPLE exam centers are listed for the United States on CAPLE."""

import json
import os
import smtplib
import sys
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
        city = str(c.get("city") or "").strip()
        name = str(c.get("name") or "").strip()
        if city or name:
            centers.append({"city": city, "name": name})
    return centers


def send_email(subject, body):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    # Falls back to sending to the sender's own address if NOTIFY_EMAIL isn't set
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

    # Compare as sorted JSON strings so order differences don't trigger false positives
    prev_json = json.dumps(prev_centers, sort_keys=True)
    curr_json = json.dumps(centers, sort_keys=True)
    listings_changed = prev_json != curr_json

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
