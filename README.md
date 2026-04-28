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

### Running manually

To test the workflow without waiting for the daily schedule, trigger it from the GitHub Actions UI. Check the **"Send test email even if nothing changed"** box to force an alert regardless of whether anything has changed.

You can also run the script locally:

```bash
GMAIL_USER=you@gmail.com \
GMAIL_APP_PASSWORD=your_app_password \
NOTIFY_EMAIL=you@gmail.com \
FORCE_NOTIFY=true \
python check_dates.py
```
