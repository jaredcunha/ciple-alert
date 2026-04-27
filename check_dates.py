#!/usr/bin/env python3
"""Check CAPLE registration site for CIPLE exam listings in the United States."""

import asyncio
import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

STATE_FILE = Path("state.json")
US_COUNTRY_VALUE = "3"
REGISTRATION_URL = "https://caple.letras.ulisboa.pt/inscricao"


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"centers": [], "last_checked": None}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


async def scrape_us_listings():
    """Navigate the CAPLE registration form and return US CIPLE exam centers/dates."""
    centers = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"Loading {REGISTRATION_URL}...")
        await page.goto(REGISTRATION_URL, wait_until="networkidle", timeout=30000)

        # --- Step 1: Select CIPLE exam ---
        print("Selecting CIPLE exam type...")
        selects = page.locator("select")
        select_count = await selects.count()
        print(f"  Found {select_count} select element(s) on page")

        exam_select = selects.first
        all_options = await exam_select.locator("option").all()

        ciple_value = None
        print("  Exam dropdown options:")
        for opt in all_options:
            text = (await opt.inner_text()).strip()
            val = await opt.get_attribute("value")
            print(f"    '{text}' (value='{val}')")
            if "CIPLE" in text.upper() and ciple_value is None:
                ciple_value = val

        if ciple_value is None:
            raise ValueError("CIPLE option not found in exam dropdown")

        print(f"  Selecting CIPLE (value='{ciple_value}')...")
        await exam_select.select_option(value=ciple_value)
        await page.wait_for_load_state("networkidle", timeout=15000)

        # --- Step 2: Select United States ---
        print(f"Selecting United States (value={US_COUNTRY_VALUE})...")

        country_select = None
        for i in range(await selects.count()):
            sel = selects.nth(i)
            opt_values = await sel.locator("option").evaluate_all(
                "opts => opts.map(o => o.value)"
            )
            if US_COUNTRY_VALUE in opt_values:
                country_select = sel
                print(f"  Country dropdown found at select index {i}")
                break

        if country_select is None:
            raise ValueError(
                f"No select element found with option value='{US_COUNTRY_VALUE}' (United States)"
            )

        await country_select.select_option(value=US_COUNTRY_VALUE)
        await page.wait_for_load_state("networkidle", timeout=15000)

        # --- Step 3: Proceed to center listing ---
        print("Looking for Next/Submit button...")
        submit = page.locator(
            "button[type='submit'], input[type='submit'],"
            " button:text-matches('continuar|seguinte|avançar|next', 'i')"
        )

        if await submit.count() > 0:
            print("  Clicking submit...")
            await submit.first.click()
            await page.wait_for_load_state("networkidle", timeout=20000)
        else:
            print("  No submit button found — checking current page for centers")

        # --- Step 4: Extract center listings ---
        print("Extracting exam centers...")
        page_text = await page.inner_text("body")

        # Check for explicit "no results" messages
        no_listing_phrases = [
            "não há", "não existem", "no centers", "nenhum centro",
            "sem resultados", "no results", "no exams available",
        ]
        if any(phrase in page_text.lower() for phrase in no_listing_phrases):
            print("  Page reports no centers available for this selection")
            await browser.close()
            return []

        # The center listing page (step 2) renders centers via Angular ng-repeat.
        # Each center row contains center.Center.city and center.Center.name.
        # Try ng-repeat rows first, then fall back to select options.
        ng_rows = await page.locator(
            "[ng-repeat*='center' i], [ng-repeat*='lape' i], [ng-repeat*='Center' i]"
        ).all()

        if ng_rows:
            print(f"  Found {len(ng_rows)} ng-repeat row(s)")
            for row in ng_rows:
                text = (await row.inner_text()).strip()
                if not text:
                    continue
                # Try to split city / name if both appear on separate lines or elements
                city_el = await row.locator("[class*='city' i], [ng-bind*='city' i]").all()
                name_el = await row.locator("[class*='name' i], [ng-bind*='name' i]").all()
                city = (await city_el[0].inner_text()).strip() if city_el else ""
                name = (await name_el[0].inner_text()).strip() if name_el else ""
                centers.append({
                    "city": city or text,
                    "name": name,
                })
        else:
            # Fall back: look for options in a select dropdown (centers as a <select>)
            current_selects = page.locator("select")
            for i in range(await current_selects.count()):
                sel = current_selects.nth(i)
                opts = await sel.locator("option").all()
                candidates = []
                for opt in opts:
                    val = await opt.get_attribute("value")
                    text = (await opt.inner_text()).strip()
                    if val and val not in ("", "0") and text:
                        # Options often read "City — Center Name" or "City (Center Name)"
                        candidates.append({"city": text, "name": "", "value": val})
                if candidates:
                    print(f"  Found {len(candidates)} option(s) in select index {i}")
                    centers = candidates
                    break

        # If we still have nothing, dump a page excerpt for debugging
        if not centers:
            print("  No structured center data found. Page excerpt (first 800 chars):")
            print(page_text[:800])

        await browser.close()

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
        if city and name:
            lines.append(f"  - {city} — {name}")
        else:
            lines.append(f"  - {city or name or 'Unknown'}")
        for session in c.get("sessions", []):
            lines.append(f"      {session}")
    return "\n".join(lines)


async def main():
    force_notify = os.environ.get("FORCE_NOTIFY", "").lower() in ("true", "1", "yes")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"=== CIPLE US Date Checker — {now} ===")
    if force_notify:
        print("Force notify mode: ON")

    state = load_state()
    prev_centers = state.get("centers", [])

    try:
        centers = await scrape_us_listings()
    except Exception as e:
        print(f"ERROR: Scrape failed: {e}")
        sys.exit(1)

    print(f"\nPrevious center count: {len(prev_centers)}")
    print(f"Current center count:  {len(centers)}")

    prev_json = json.dumps(prev_centers, sort_keys=True)
    curr_json = json.dumps(centers, sort_keys=True)
    listings_changed = prev_json != curr_json

    print(f"Listings changed: {listings_changed}")

    # Persist updated state
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
        if centers:
            body = (
                f"Test notification from the CIPLE exam date checker.\n\n"
                f"Status: {len(centers)} US exam center(s) currently listed:\n\n"
                f"{format_centers(centers)}\n\n"
                f"Registration: {REGISTRATION_URL}\n\n"
                f"Checked: {now}"
            )
        else:
            body = (
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
    asyncio.run(main())
