#!/usr/bin/env python3
"""
Train Cancellation Bot – GitHub Actions version (Transport API)
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
ORIGIN = "MIA"               # Manchester Airport
DESTINATION = "BIF"          # Barrow-in-Furness
START_TIME = time(6, 0)
END_TIME = time(16, 0)

TRANSPORTAPI_BASE = "https://transportapi.com/v3/uk"
APP_ID = os.environ.get("TRANSPORTAPI_APP_ID")
APP_KEY = os.environ.get("TRANSPORTAPI_APP_KEY")

NOTIFIED_FILE = Path("notified_cancellations.json")

SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("train_bot")


def is_weekday():
    return datetime.now().weekday() < 5


def fetch_departures(origin):
    """
    Fetch ALL live departures from `origin` station.
    We will filter by destination later.
    """
    if not APP_ID or not APP_KEY:
        log.error("Missing Transport API credentials")
        return []

    url = f"{TRANSPORTAPI_BASE}/train/station/{origin}/live.json"
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "darwin": "true",          # include cancellation reasons
    }

    log.info("Requesting live departures from %s", origin)
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error("Failed to fetch departures: %s", e)
        return []

    departures = data.get("departures", {}).get("all", [])
    if not departures:
        log.info("No departures returned from Transport API.")
    return departures


def filter_services(services, destination_crs, end_time):
    """
    Keep services:
    - heading to destination_crs
    - scheduled departure >= START_TIME (implicitly, because we request around 06:00)
    - scheduled departure < end_time
    """
    filtered = []
    for svc in services:
        # Destination check
        dest = svc.get("destination_name")  # e.g., "Barrow-in-Furness"
        dest_code = svc.get("destination_code")  # CRS code, e.g., "BIF"
        if dest_code != destination_crs:
            continue

        # Scheduled departure time
        aimed = svc.get("aimed_departure_time")  # "07:30"
        if not aimed:
            continue
        try:
            dep_time = datetime.strptime(aimed, "%H:%M").time()
        except ValueError:
            continue
        if dep_time < START_TIME or dep_time >= end_time:
            continue

        filtered.append(svc)
    return filtered


def load_notified_ids(filepath):
    if not filepath.exists():
        return set()
    try:
        with open(filepath, "r") as f:
            return set(json.load(f).get("ids", []))
    except Exception:
        log.exception("Could not read notified file, starting fresh.")
        return set()


def save_notified_ids(filepath, ids):
    try:
        with open(filepath, "w") as f:
            json.dump({"ids": sorted(ids)}, f, indent=2)
    except Exception:
        log.exception("Failed to write notified file.")


def send_email(subject, body):
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        log.error("Missing email credentials")
        return
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as srv:
            srv.starttls()
            srv.login(SENDER_EMAIL, SENDER_PASSWORD)
            srv.send_message(msg)
        log.info("Email sent.")
    except Exception:
        log.exception("Email failed.")


def main():
    if not is_weekday():
        log.info("Not a weekday – exiting.")
        return

    notified = load_notified_ids(NOTIFIED_FILE)
    raw_services = fetch_departures(ORIGIN)
    targeted = filter_services(raw_services, DESTINATION, END_TIME)
    log.info("Found %d trains to %s in the time window.", len(targeted), DESTINATION)

    new_cancelled = []
    for svc in targeted:
        if svc.get("status") == "CANCELLED":
            uid = svc.get("service_uid", "unknown")
            train_date = svc.get("date", datetime.now().strftime("%Y-%m-%d"))
            key = f"{uid}_{train_date}"
            if key not in notified:
                new_cancelled.append(svc)
                notified.add(key)

    if not new_cancelled:
        log.info("No new cancellations.")
    else:
        log.info("Detected %d new cancellation(s).", len(new_cancelled))
        for svc in new_cancelled:
            aimed = svc.get("aimed_departure_time", "??:??")
            reason = svc.get("cancel_reason", "No reason given")
            dest_name = svc.get("destination_name", DESTINATION)
            subject = f"🚫 TRAIN CANCELLED: {aimed} MIA → {dest_name}"
            body = (
                f"Scheduled departure: {aimed}\n"
                f"Destination: {dest_name}\n"
                f"Reason: {reason}\n"
                f"Service UID: {svc.get('service_uid', 'unknown')}\n"
                f"\n-- Train Cancel Bot"
            )
            send_email(subject, body)

    save_notified_ids(NOTIFIED_FILE, notified)


if __name__ == "__main__":
    main()