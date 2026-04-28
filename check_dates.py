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


async def dismiss_overlays(page):
    """Dismiss cookie consent banner and any modal dialogs."""
    # Cookie consent — try common button selectors
    try:
        consent = page.locator(
            ".cc-btn.cc-allow, .cc-btn.cc-dismiss, .cc-accept, "
            ".cc-window button, [aria-label='cookieconsent'] button"
        )
        if await consent.count() > 0:
            await consent.first.click(timeout=3000)
            await page.wait_for_timeout(500)
            print("  Dismissed cookie consent banner")
    except Exception:
        pass

    # Modal dialogs — press Escape first, then try clicking the background
    try:
        if await page.locator(".modal.is-active").count() > 0:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
            print("  Dismissed modal via Escape")
    except Exception:
        pass

    try:
        bg = page.locator(".modal-background")
        if await bg.count() > 0:
            await bg.first.click(timeout=2000)
            await page.wait_for_timeout(300)
    except Exception:
        pass


async def scrape_us_listings():
    """Navigate the CAPLE registration form and return US CIPLE exam centers/dates."""
    centers = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"Loading {REGISTRATION_URL}...")
        await page.goto(REGISTRATION_URL, wait_until="networkidle", timeout=30000)
        await dismiss_overlays(page)

        # --- Step 1: Select CIPLE exam ---
        print("Selecting CIPLE exam type...")
        selects = page.locator("select")
        select_count = await selects.count()
        print(f"  Found {select_count} select element(s) on page")

        exam_select = selects.first
        all_options = await exam_select.locator("option").all()

        # Angular binds complex objects so option values show as "[object Object]".
        # Select by visible label text instead.
        ciple_label = None
        print("  Exam dropdown options:")
        for opt in all_options:
            text = (await opt.inner_text()).strip()
            val = await opt.get_attribute("value")
            print(f"    '{text}' (value='{val}')")
            # Match "CIPLE" exactly, not "CIPLE-e" or other variants
            if text.upper() == "CIPLE" and ciple_label is None:
                ciple_label = text

        if ciple_label is None:
            raise ValueError("CIPLE option not found in exam dropdown")

        print(f"  Selecting '{ciple_label}' by label...")
        await exam_select.select_option(label=ciple_label)
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

        # After selecting the country the app opens a modal containing the center list.
        # The modal stays open (it IS the center-selection UI, not a loading spinner).
        # Wait for at least one country-item label to appear inside it.
        print("  Waiting for center modal to appear...")
        try:
            await page.wait_for_selector(
                ".modal.is-active label.radio.country-item",
                state="visible",
                timeout=15000,
            )
            print("  Center modal is visible")
        except PlaywrightTimeoutError:
            print("  No centers appeared in modal — none available for this selection")
            await browser.close()
            return []

        # --- Step 3: Extract centers from modal ---
        # Each center is a <label class="radio country-item"> with .column divs.
        # The is-1 column holds the radio button; remaining columns hold city / name.
        print("Extracting exam centers from modal...")
        center_labels = await page.locator(
            ".modal.is-active label.radio.country-item"
        ).all()
        print(f"  Found {len(center_labels)} center label(s)")

        for label in center_labels:
            columns = await label.locator(".column:not(.is-1)").all()
            parts = []
            for col in columns:
                t = (await col.inner_text()).strip()
                if t:
                    parts.append(t)

            if not parts:
                parts = [(await label.inner_text()).strip()]

            city = parts[0] if len(parts) > 0 else ""
            name = parts[1] if len(parts) > 1 else ""
            centers.append({"city": city, "name": name})

        if not centers:
            page_text = await page.inner_text("body")
            print("  No centers parsed. Page excerpt (first 800 chars):")
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
