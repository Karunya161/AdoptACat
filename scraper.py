import os
import json
import time
import re
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_IDS = [cid.strip() for cid in os.environ["TELEGRAM_CHAT_IDS"].split(",")]

POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "30"))
TARGET_URL = "https://spcala.com/adoptable/?type=Cat"
SEEN_FILE = Path("seen_cats.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def fetch_cats() -> list[dict]:
    resp = requests.get(TARGET_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    cats = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = re.search(r"ss=(LACA-A-\d+)", href)
        if not match:
            continue

        animal_id = match.group(1)

        # Name is inside the <a> tag
        name = link.get_text(strip=True) or "Unknown"

        # Breed and age are in plain text siblings after the <a> tag
        parent = link.parent
        parent_text = parent.get_text(separator=" ", strip=True) if parent else ""
        # Remove the name from the parent text to isolate breed/age info
        extra = parent_text.replace(name, "").strip()
        # First word-chunk is the breed (e.g. "Domestic Shorthair")
        lines = [l.strip() for l in extra.splitlines() if l.strip()]
        breed = lines[0] if lines else extra.split("  ")[0].strip()

        cats.append({
            "id": animal_id,
            "name": name,
            "breed": breed,
            "text": parent_text,
            "url": f"https://spcala.com/adoptable/?ss={animal_id}",
        })

    seen_ids: set = set()
    unique = []
    for cat in cats:
        if cat["id"] not in seen_ids:
            seen_ids.add(cat["id"])
            unique.append(cat)
    return unique


def is_siamese(cat: dict) -> bool:
    haystack = (cat["breed"] + " " + cat["text"]).lower()
    return "siamese" in haystack


def send_notification(cat: dict) -> None:
    text = (
        f"New Siamese cat available at spcaLA!\n\n"
        f"Name: {cat['name']}\n"
        f"Breed: {cat['breed']}\n"
        f"{cat['url']}"
    )
    for chat_id in TELEGRAM_CHAT_IDS:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
    log.info("Telegram message sent to %d recipient(s) for %s (%s)", len(TELEGRAM_CHAT_IDS), cat["name"], cat["id"])


def check_once() -> None:
    log.info("Checking %s ...", TARGET_URL)
    try:
        cats = fetch_cats()
    except Exception as exc:
        log.error("Failed to fetch page: %s", exc)
        return

    log.info("Found %d cat listing(s) on page", len(cats))
    seen = load_seen()
    new_siamese = [c for c in cats if is_siamese(c) and c["id"] not in seen]

    if not new_siamese:
        log.info("No new Siamese cats found")
    else:
        for cat in new_siamese:
            log.info("NEW Siamese cat: %s (%s)", cat["name"], cat["id"])
            send_notification(cat)
            seen.add(cat["id"])
        save_seen(seen)


def main() -> None:
    log.info(
        "CatHunt started - polling every %d minute(s). Press Ctrl+C to stop.",
        POLL_INTERVAL_MINUTES,
    )

    if not SEEN_FILE.exists():
        log.info("First run - recording all current listings as already seen.")
        try:
            cats = fetch_cats()
            save_seen({c["id"] for c in cats})
            log.info("Saved %d existing listing(s). Watching for new Siamese cats...", len(cats))
        except Exception as exc:
            log.error("Could not seed seen list: %s", exc)

    while True:
        check_once()
        log.info("Sleeping %d minute(s)...", POLL_INTERVAL_MINUTES)
        time.sleep(POLL_INTERVAL_MINUTES * 60)


def test_mode() -> None:
    log.info("--- TEST MODE ---")

    # 1. Send a real Telegram DM to all recipients
    log.info("Sending test Telegram message to %d recipient(s)...", len(TELEGRAM_CHAT_IDS))
    for chat_id in TELEGRAM_CHAT_IDS:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": "CatHunt is working! You'll get a message like this when a Siamese cat is listed."},
            timeout=10,
        )
        resp.raise_for_status()
    log.info("Telegram test message sent — check your phones!")

    # 2. Scrape the page and print all cats found
    log.info("Scraping %s ...", TARGET_URL)
    cats = fetch_cats()
    log.info("Found %d cat(s):", len(cats))
    for cat in cats:
        siamese_flag = " <-- SIAMESE" if is_siamese(cat) else ""
        log.info("  [%s] %s — %s%s", cat["id"], cat["name"], cat["breed"], siamese_flag)


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        test_mode()
    else:
        main()
