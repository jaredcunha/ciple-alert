# CIPLE Alert

A GitHub Actions bot that watches the [CAPLE registration page](https://caple.letras.ulisboa.pt/inscricao) and sends an email notification when CIPLE exam centers become available in the United States.

## What it does

CAPLE (the Portuguese language proficiency testing authority) lists exam centers by country through their API. US listings are often absent for long stretches of time, which means you have to check manually and repeatedly to catch the window when registration opens.

This tool automates that. Once a day it fetches the current list of US exam centers for the CIPLE exam and compares it against the last known state. If new centers appear, it sends an email alert so you can register before spots fill up.

## How it works

- **`check_dates.py`** — fetches the CAPLE centers API, diffs the result against `state.json`, and sends an email via Gmail SMTP if anything changed
- **`state.json`** — persists the last-known list of centers; committed back to the repo after each run so the next run has something to compare against
- **`.github/workflows/check-ciple.yml`** — runs the checker daily at 6am ET via GitHub Actions

## Setup

### Secrets

Add the following secrets to your GitHub repository (Settings → Secrets and variables → Actions):

| Secret | Description |
|---|---|
| `GMAIL_USER` | Gmail address used to send alerts |
| `GMAIL_APP_PASSWORD` | [Gmail App Password](https://support.google.com/accounts/answer/185833) (not your regular password) |
| `NOTIFY_EMAIL` | Address to receive alerts (can be the same as `GMAIL_USER`) |
| `PUSHOVER_TOKEN` | *(optional)* Pushover Application/API Token |
| `PUSHOVER_USER` | *(optional)* Pushover User Key |

#### Push notifications via Pushover

Setting `PUSHOVER_TOKEN` and `PUSHOVER_USER` sends a second, shortened copy of every alert as a push notification through [Pushover](https://pushover.net) in addition to the email.

1. Create a free Pushover account and copy your **User Key** from the dashboard.
2. Create an Application/API Token (dashboard → "Create an Application/API Token") and copy the token it generates.
3. Install the Pushover app on your phone and log in with the same account (30-day free trial, then a one-time ~$5 purchase per platform — no subscription).

### Running manually

To test the workflow without waiting for the daily schedule, trigger it from the GitHub Actions UI. Check the **"Send test email even if nothing changed"** box to force an alert regardless of whether anything has changed.

You can also run the script locally:

```bash
GMAIL_USER=you@gmail.com \
GMAIL_APP_PASSWORD=your_app_password \
NOTIFY_EMAIL=you@gmail.com \
PUSHOVER_TOKEN=your_pushover_api_token \
PUSHOVER_USER=your_pushover_user_key \
FORCE_NOTIFY=true \
python check_dates.py
```
