# Sagrada Família ticket watcher

This small, dependency-free Python program checks the official ticket calendar
for **September 30 and October 1, 2026** every 30 minutes. Any entry time counts.
It only checks availability; it does not reserve or buy tickets.

The included GitHub Actions workflow sends a short, calm, consigliere-style
Telegram status report after every check—even when nothing has changed. An
available result is worded more urgently and includes the booking link.

## Run it

Python 3.9 or newer is sufficient:

```bash
cd /Users/andriibutko/projects/wisedocs/sagrada-ticket-watcher
python3 watcher.py
```

Keep that terminal/process running. Stop it with `Ctrl-C`. On macOS, the first
newly available date produces a Notification Center alert. Availability is also
written to the terminal every time it checks.

Useful options:

```bash
# Check once, without starting the 30-minute loop
python3 watcher.py --once

# Require at least two tickets
python3 watcher.py --tickets 2

# Send a notification after every check, including unavailable results
python3 watcher.py --report-every-check

# Override dates or interval
python3 watcher.py --dates 2026-09-30 2026-10-01 --interval 1800
```

## Telegram alerts

This uses Telegram's Bot API directly; you do not need to host an incoming
webhook server.

1. Open `@BotFather` in Telegram, run `/newbot`, and copy the bot token.
2. Send your new bot a message such as `/start`.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
   copy `message.chat.id` from the response.
4. Set both values and send a test:

```bash
export TELEGRAM_BOT_TOKEN='123456:replace-with-your-token'
export TELEGRAM_CHAT_ID='replace-with-your-chat-id'
python3 watcher.py --test-notification --no-desktop-notification
```

For GitHub Actions, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` under
**Settings → Secrets and variables → Actions**. The included workflow reads
both secrets automatically. To verify them, open **Actions**, select
**Check Sagrada Familia tickets**, click **Run workflow**, and enable
**Send a test notification instead of checking tickets**.

## Optional Slack/Discord alerts

Set a Slack or Discord incoming-webhook URL before starting the watcher:

```bash
export SAGRADA_WEBHOOK_URL='https://your-webhook-url'
python3 watcher.py
```

The URL is read from the environment and is never stored by the program.

## Behavior

- The checker makes one token request and one calendar request per target month
  every 30 minutes (three requests total for these two dates).
- An alert is sent when a date changes from unavailable to available. It will
  alert again if availability disappears and later returns.
- State is kept in `.watcher-state.json`; delete that file if you want the next
  available result to alert again immediately.
- If a request fails, the watcher logs the error, keeps the previous state, and
  retries at the next interval.

Run the tests with:

```bash
python3 -m unittest discover -s tests -v
```
