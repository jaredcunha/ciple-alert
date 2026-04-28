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

        # Capture all JSON responses — Angular fetches center data via XHR when
        # the country is selected, so we read from the API rather than the DOM.
        json_responses: list[dict] = []

        async def capture_response(response):
            if "json" in response.headers.get("content-type", ""):
                try:
                    data = await response.json()
                    json_responses.append({"url": response.url, "data": data})
                    print(f"  [API] {response.url}")
                except Exception:
                    pass

        page.on("response", capture_response)

        print(f"Loading {REGISTRATION_URL}...")
        await page.goto(REGISTRATION_URL, wait_until="networkidle", timeout=30000)
        await dismiss_overlays(page)

        # --- Step 1: Select CIPLE exam ---
        print("Selecting CIPLE exam type...")
        selects = page.locator("select")
        print(f"  Found {await selects.count()} select element(s) on page")

        exam_select = selects.first
        all_options = await exam_select.locator("option").all()

        # Angular binds complex objects so option values show as "[object Object]".
        # Select by visible label text instead.
        ciple_label = None
        for opt in all_options:
            text = (await opt.inner_text()).strip()
            val = await opt.get_attribute("value")
            print(f"    '{text}' (value='{val}')")
            if text.upper() == "CIPLE" and ciple_label is None:
                ciple_label = text

        if ciple_label is None:
            raise ValueError("CIPLE option not found in exam dropdown")

        print(f"  Selecting '{ciple_label}' by label...")
        await exam_select.select_option(label=ciple_label)
        await page.wait_for_load_state("networkidle", timeout=15000)

        # --- Step 2: Select country ---
        print(f"Selecting country (value={US_COUNTRY_VALUE})...")

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
                f"No select element found with option value='{US_COUNTRY_VALUE}'"
            )

        json_responses.clear()  # only want responses triggered by country selection
        await country_select.select_option(value=US_COUNTRY_VALUE)
        await page.wait_for_load_state("networkidle", timeout=15000)

        print(f"  Captured {len(json_responses)} JSON response(s) after country selection")

        # --- Step 3: Parse centers from API response ---
        # Angular calls an endpoint that returns a list of center objects shaped like:
        # [{"Center": {"city": "...", "name": "..."}}, ...]
        # Walk every captured response and pick the first one that looks like centers.
        for resp in json_responses:
            data = resp["data"]
            if not isinstance(data, list) or not data:
                continue
            first = data[0]
            if not isinstance(first, dict):
                continue

            # Detect center-shaped payloads
            center_obj = first.get("Center", first)
            if not isinstance(center_obj, dict):
                continue
            if not any(k in center_obj for k in ("city", "name", "City", "Name")):
                continue

            print(f"  Using center data from: {resp['url']}")
            for item in data:
                c = item.get("Center", item)
                city = c.get("city") or c.get("City") or ""
                name = c.get("name") or c.get("Name") or ""
                if city or name:
                    centers.append({"city": str(city).strip(), "name": str(name).strip()})
            break

        # --- Fallback: try CSS selector on the DOM ---
        if not centers:
            print("  No centers in API responses — trying DOM selector...")
            try:
                await page.wait_for_selector(
                    "label.radio.country-item", state="visible", timeout=8000
                )
            except PlaywrightTimeoutError:
                pass

            for label in await page.locator("label.radio.country-item").all():
                columns = await label.locator(".column:not(.is-1)").all()
                parts = [
                    t for col in columns
                    if (t := (await col.inner_text()).strip())
                ]
                if not parts:
                    parts = [(await label.inner_text()).strip()]
                centers.append({
                    "city": parts[0] if parts else "",
                    "name": parts[1] if len(parts) > 1 else "",
                })

        if not centers:
            page_text = await page.inner_text("body")
            print("  No centers found anywhere. Page excerpt (first 800 chars):")
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
