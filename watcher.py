#!/usr/bin/env python3
"""Check Sagrada Familia's public calendar for ticket availability."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TICKET_URL = (
    "https://tickets.sagradafamilia.org/en/1-individual/"
    "4375-sagrada-familia"
)
TOKEN_URL = "https://services.clorian.com/user/api/oauth/token"
AVAILABILITY_URL = (
    "https://services.clorian.com/catalog/salesGroups/1/"
    "product/4375/availability"
)
SECRET_KEY = "thesagradafamiliafrontendoftomorrow"
POS_ID = "649"
VENUE_ID = "1"
DEFAULT_DATES = (date(2026, 9, 30), date(2026, 10, 1))
DEFAULT_INTERVAL_SECONDS = 30 * 60
ORIGIN = "https://tickets.sagradafamilia.org"
USER_AGENT = "SagradaTicketWatcher/1.0 (+personal availability checker)"

UrlOpen = Callable[..., Any]


class WatcherError(RuntimeError):
    """Raised when the ticket service returns an unusable response."""


@dataclass(frozen=True)
class CheckResult:
    checked_at: str
    statuses: dict[str, str]

    @property
    def available_dates(self) -> list[str]:
        return sorted(
            day for day, status in self.statuses.items() if status == "availability"
        )


class TicketClient:
    def __init__(self, opener: UrlOpen = urlopen, timeout: float = 20.0) -> None:
        self.opener = opener
        self.timeout = timeout

    def _json_request(self, request: Request) -> Any:
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WatcherError(f"ticket service returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise WatcherError(f"could not read the ticket service: {exc}") from exc

    def get_token(self) -> str:
        url = f"{TOKEN_URL}?{urlencode({'secretKey': SECRET_KEY})}"
        request = Request(url, method="POST", headers=browser_headers())
        payload = self._json_request(request)
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise WatcherError("ticket service did not return an access token")
        return str(token)

    def month_availability(
        self, token: str, year: int, month: int, tickets: int
    ) -> dict[str, str]:
        query = urlencode(
            {
                "year": year,
                "month": month,
                "venueId": VENUE_ID,
                "minTickets": tickets,
            }
        )
        headers = browser_headers()
        headers.update({"Authorization": f"Bearer {token}", "pos": POS_ID})
        payload = self._json_request(Request(f"{AVAILABILITY_URL}?{query}", headers=headers))
        if not isinstance(payload, dict):
            raise WatcherError("ticket service returned an unexpected availability response")
        if payload.get("errorCode"):
            raise WatcherError(
                f"ticket service error {payload['errorCode']}: "
                f"{payload.get('message', 'unknown error')}"
            )
        return {str(day): str(status) for day, status in payload.items()}

    def check(self, target_dates: Iterable[date], tickets: int) -> CheckResult:
        targets = tuple(target_dates)
        token = self.get_token()
        months = sorted({(target.year, target.month) for target in targets})
        calendar: dict[str, str] = {}
        for year, month in months:
            calendar.update(self.month_availability(token, year, month, tickets))
        statuses = {
            target.isoformat(): calendar.get(target.isoformat(), "unknown")
            for target in targets
        }
        return CheckResult(
            checked_at=datetime.now(timezone.utc).isoformat(), statuses=statuses
        )


def browser_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Accept-Language": "en",
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/",
        "User-Agent": USER_AGENT,
    }


def parse_dates(values: Iterable[str]) -> tuple[date, ...]:
    try:
        parsed = tuple(date.fromisoformat(value) for value in values)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"dates must use YYYY-MM-DD: {exc}") from exc
    if not parsed:
        raise argparse.ArgumentTypeError("at least one date is required")
    return parsed


def load_state(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Ignoring unreadable state file %s: %s", path, exc)
        return {}
    statuses = payload.get("statuses", {}) if isinstance(payload, dict) else {}
    return statuses if isinstance(statuses, dict) else {}


def save_state(path: Path, result: CheckResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(
            {"checked_at": result.checked_at, "statuses": result.statuses}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def newly_available(previous: dict[str, str], current: dict[str, str]) -> list[str]:
    return sorted(
        day
        for day, status in current.items()
        if status == "availability" and previous.get(day) != "availability"
    )


def desktop_notification(message: str) -> None:
    if platform.system() != "Darwin" or not shutil.which("osascript"):
        return
    script = (
        "on run argv\n"
        "display notification (item 1 of argv) with title (item 2 of argv)\n"
        "end run"
    )
    subprocess.run(
        ["osascript", "-e", script, message, "Sagrada Família tickets"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def webhook_notification(url: str, message: str) -> None:
    # The combined payload works with Slack (text) and Discord (content).
    body = json.dumps(
        {"text": message, "content": message, "url": TICKET_URL}
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=20) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        logging.error("Could not send webhook alert: %s", exc)


def telegram_notification(
    bot_token: str, chat_id: str, message: str, opener: UrlOpen = urlopen
) -> bool:
    """Send an alert through Telegram's Bot API without exposing the token."""
    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": message,
            "link_preview_options": {"is_disabled": True},
        }
    ).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with opener(request, timeout=20) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not payload.get("ok"):
            logging.error("Telegram rejected the alert")
            return False
        return True
    except HTTPError as exc:
        # HTTPError's URL contains the bot token, so deliberately do not log it.
        logging.error("Telegram returned HTTP %s while sending the alert", exc.code)
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        logging.error("Could not send Telegram alert: %s", exc)
    return False


def send_notifications(args: argparse.Namespace, message: str) -> None:
    if not args.no_desktop_notification:
        desktop_notification(message)
    if args.webhook_url:
        webhook_notification(args.webhook_url, message)
    if args.telegram_bot_token and args.telegram_chat_id:
        telegram_notification(args.telegram_bot_token, args.telegram_chat_id, message)


def run_check(args: argparse.Namespace, client: TicketClient) -> None:
    result = client.check(args.dates, args.tickets)
    previous = load_state(args.state_file)
    changed = newly_available(previous, result.statuses)
    summary = ", ".join(
        f"{day}: {status}" for day, status in sorted(result.statuses.items())
    )
    logging.info("Availability check — %s", summary)

    if changed:
        days = ", ".join(changed)
        message = (
            f"Tickets are available on {days} for at least {args.tickets} "
            f"visitor(s). Book now: {TICKET_URL}"
        )
        logging.warning(message)
        send_notifications(args, message)

    save_state(args.state_file, result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dates",
        nargs="+",
        default=[day.isoformat() for day in DEFAULT_DATES],
        metavar="YYYY-MM-DD",
        help="dates to watch (default: 2026-09-30 2026-10-01)",
    )
    parser.add_argument(
        "--tickets",
        type=int,
        default=1,
        help="minimum number of tickets needed (default: 1)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help="seconds between checks (default: 1800)",
    )
    parser.add_argument(
        "--once", action="store_true", help="check once instead of running continuously"
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(__file__).resolve().with_name(".watcher-state.json"),
        help="file used to suppress duplicate alerts",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("SAGRADA_WEBHOOK_URL", ""),
        help="optional Slack/Discord webhook (or SAGRADA_WEBHOOK_URL)",
    )
    parser.add_argument(
        "--telegram-bot-token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        help="optional Telegram bot token (or TELEGRAM_BOT_TOKEN)",
    )
    parser.add_argument(
        "--telegram-chat-id",
        default=os.environ.get("TELEGRAM_CHAT_ID", ""),
        help="Telegram target chat ID (or TELEGRAM_CHAT_ID)",
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="send a test alert and exit without checking tickets",
    )
    parser.add_argument(
        "--no-desktop-notification",
        action="store_true",
        help="disable native macOS notifications",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.dates = parse_dates(args.dates)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if args.tickets < 1:
        parser.error("--tickets must be at least 1")
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")
    if bool(args.telegram_bot_token) != bool(args.telegram_chat_id):
        parser.error(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be provided together"
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.test_notification:
        send_notifications(
            args,
            f"Sagrada Família ticket watcher is configured correctly. {TICKET_URL}",
        )
        return 0
    client = TicketClient()

    while True:
        started = time.monotonic()
        try:
            run_check(args, client)
        except WatcherError as exc:
            logging.error("Check failed: %s", exc)
            if args.once:
                return 1
        if args.once:
            return 0
        elapsed = time.monotonic() - started
        time.sleep(max(0, args.interval - elapsed))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130)
