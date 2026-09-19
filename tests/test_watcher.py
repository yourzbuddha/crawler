import io
import json
import unittest
from datetime import date

import watcher


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class FakeOpener:
    def __init__(self):
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if request.full_url.startswith(watcher.TOKEN_URL):
            payload = {"access_token": "test-token"}
        elif "month=9" in request.full_url:
            payload = {"2026-09-30": "no-availability"}
        else:
            payload = {"2026-10-01": "availability"}
        return FakeResponse(json.dumps(payload).encode())


class WatcherTests(unittest.TestCase):
    def test_checks_each_month_and_selects_target_dates(self):
        opener = FakeOpener()
        result = watcher.TicketClient(opener=opener).check(
            (date(2026, 9, 30), date(2026, 10, 1)), tickets=2
        )

        self.assertEqual(
            result.statuses,
            {
                "2026-09-30": "no-availability",
                "2026-10-01": "availability",
            },
        )
        self.assertEqual(result.available_dates, ["2026-10-01"])
        self.assertEqual(len(opener.requests), 3)
        self.assertIn("minTickets=2", opener.requests[1][0].full_url)
        self.assertEqual(
            opener.requests[1][0].get_header("Authorization"), "Bearer test-token"
        )
        self.assertEqual(opener.requests[1][0].get_header("Pos"), watcher.POS_ID)

    def test_newly_available_only_reports_transitions(self):
        previous = {
            "2026-09-30": "availability",
            "2026-10-01": "no-availability",
        }
        current = {
            "2026-09-30": "availability",
            "2026-10-01": "availability",
        }
        self.assertEqual(watcher.newly_available(previous, current), ["2026-10-01"])

    def test_parse_dates_rejects_bad_input(self):
        with self.assertRaises(Exception):
            watcher.parse_dates(["09/30/2026"])

    def test_telegram_notification_uses_send_message(self):
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(b'{"ok": true, "result": {}}')

        sent = watcher.telegram_notification(
            "secret-token", "123456", "Tickets available", opener=opener
        )

        self.assertTrue(sent)
        self.assertEqual(len(requests), 1)
        request, timeout = requests[0]
        self.assertEqual(timeout, 20)
        self.assertEqual(
            request.full_url,
            "https://api.telegram.org/botsecret-token/sendMessage",
        )
        self.assertEqual(
            json.loads(request.data),
            {
                "chat_id": "123456",
                "text": "Tickets available",
                "link_preview_options": {"is_disabled": True},
            },
        )


if __name__ == "__main__":
    unittest.main()
