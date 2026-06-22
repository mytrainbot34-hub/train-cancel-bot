#!/usr/bin/env python3
"""
Train Cancellation Bot
Monitors trains from Manchester Airport (MIA) to Barrow-in-Furness (BIF)
Mon-Fri, 06:00-16:00. Sends an email when a cancellation is detected.
"""

import json
import logging
import os
import smtplib
from datetime import datetime, time
from email.message import EmailMessage
from pathlib import Path

import requests

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
# Monitoring settings
ORIGIN = "MIA"               # Manchester Airport
DESTINATION = "BIF"          # Barrow-in-Furness
START_TIME = time(6, 0)      # 06:00
END_TIME = time(16, 0)       # 16:00 (departures *before* this time)
WEEKDAYS_ONLY = True         # Only run Monday-Friday

# How many minutes ahead the bot looks from the start time (should cover 10h)
TIME_WINDOW_MINUTES = 600    # 6:00 -> 16:00 = 600 minutes

# Huxley endpoint (no API key required)
HUXLEY_URL = "https://huxley.apphb.com/departures/{crs}/from"

# File to remember already notified cancellations (prevents duplicates)
NOTIFIED_FILE = Path("notified_cancellations.json")

# Email settings (replace with your own)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "mytrainbot34@gmail.com"
SENDER_PASSWORD = "Tolstoy94!"   # Use an App Password if 2FA is on
RECIPIENT_EMAIL = "upcottconnor@gmail.com"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("train_bot")

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def is_weekday() -> bool:
    """Return True if today is Monday-Friday."""
    return datetime.now().weekday() < 5  # 0=Mon, 6=Sun


def fetch_departures(origin: str, start: time, window_min: int) -> list:
    """
    Fetch all departures from `origin` station starting at `start` time,
    looking `window_min` minutes ahead.
    Returns a list of service dicts.
    """
    time_str = start.strftime("%H:%M")
    url = HUXLEY_URL.format(crs=origin)
    params = {"time": time_str, "timeWindow": window_min}
    log.info("Requesting departures: %s %s", url, params)

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("Failed to fetch departures: %s", exc)
        return []

    # The API returns a dict with "trainServices" (may be None)
    services = data.get("trainServices")
    if not services:
        log.info("No train services found in the requested window.")
        return []
    return services


def filter_services(services: list, destination_crs: str,
                    end_time: time) -> list:
    """Keep only services heading to `destination_crs` and departing before `end_time`."""
    filtered = []
    for svc in services:
        # Destination check
        dest = svc.get("destination", [{}])[0] if isinstance(svc.get("destination"), list) else svc.get("destination", {})
        if dest.get("crs") != destination_crs:
            continue

        # Scheduled departure time check
        std_str = svc.get("std")
        if not std_str:
            continue
        try:
            dep_time = datetime.strptime(std_str, "%H:%M").time()
        except ValueError:
            log.warning("Could not parse std '%s'", std_str)
            continue
        if dep_time >= end_time:
            continue

        filtered.append(svc)
    return filtered


def find_new_cancellations(services: list, notified_ids: set) -> list:
    """Return services that are cancelled and not yet notified."""
    new = []
    for svc in services:
        if svc.get("isCancelled"):
            sid = svc.get("serviceID")
            if not sid:
                continue
            key = f"{sid}_{datetime.now().strftime('%Y%m%d')}"
            if key not in notified_ids:
                new.append(svc)
                notified_ids.add(key)   # mark as seen immediately
    return new


def load_notified_ids(filepath: Path) -> set:
    """Load previously notified service IDs from a JSON file."""
    if not filepath.exists():
        return set()
    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        return set(data.get("ids", []))
    except Exception:
        log.exception("Could not read notified file, starting fresh.")
        return set()


def save_notified_ids(filepath: Path, ids: set) -> None:
    """Save notified service IDs to a JSON file."""
    try:
        with open(filepath, "w") as f:
            json.dump({"ids": sorted(ids)}, f, indent=2)
    except Exception:
        log.exception("Failed to write notified file.")


def send_email(subject: str, body: str) -> None:
    """Send an email notification."""
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        log.info("Email sent to %s", RECIPIENT_EMAIL)
    except Exception:
        log.exception("Failed to send email.")


# ----------------------------------------------------------------------
# Main monitoring routine
# ----------------------------------------------------------------------
def main():
    # Only run on weekdays if configured
    if WEEKDAYS_ONLY and not is_weekday():
        log.info("Today is not a weekday – exiting.")
        return

    # Load known cancellations
    notified = load_notified_ids(NOTIFIED_FILE)

    # 1. Get all departures from Manchester Airport in the monitoring window
    services = fetch_departures(ORIGIN, START_TIME, TIME_WINDOW_MINUTES)

    # 2. Filter to Barrow-in-Furness only, and departure before END_TIME
    targeted = filter_services(services, DESTINATION, END_TIME)
    log.info("Found %d trains to %s in the time window.", len(targeted), DESTINATION)

    # 3. Find any cancellations we haven't alerted about yet
    cancellations = find_new_cancellations(targeted, notified)

    if not cancellations:
        log.info("No new cancellations detected.")
    else:
        log.info("Detected %d new cancellation(s)!", len(cancellations))
        for svc in cancellations:
            std = svc.get("std", "??:??")
            reason = svc.get("cancelReason", "No reason given")
            dest_name = svc.get("destination", [{}])[0].get("locationName", DESTINATION) if isinstance(svc.get("destination"), list) else svc.get("destination", {}).get("locationName", DESTINATION)
            subject = f"🚫 TRAIN CANCELLED: {std} MIA → {dest_name}"
            body = (
                f"Scheduled departure: {std}\n"
                f"Destination: {dest_name}\n"
                f"Reason: {reason}\n"
                f"Service ID: {svc.get('serviceID', 'unknown')}\n"
                f"\n-- Train Cancel Bot"
            )
            send_email(subject, body)

        # Persist the updated list of notified IDs
        save_notified_ids(NOTIFIED_FILE, notified)


if __name__ == "__main__":
    main()
