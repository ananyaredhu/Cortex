"""Check #3 (docs/06-risks-and-checks.md): does the 2025 rate-limit cut apply to our internal app?

The cut limits `conversations.history` and `conversations.replies` to 1 request per minute and 15 messages per
call for commercially distributed apps outside the Marketplace. An internal app should keep the old limits
(tens of requests per minute, up to 1000 messages per call).

    python -m connectors.slack.check_rate_limit            # 12 calls, on the bot's busiest channel
    python -m connectors.slack.check_rate_limit --calls 20 --channel auth-private

The busiest channel is the default because the 15-messages-per-call cap only shows on a channel with more than 15
messages (join and leave notices count). Read-only. Prints channel names, counts and timings only; never the token,
channel IDs or message text.
"""
import argparse
import time

from connectors.env import load_dotenv
from connectors.slack.client import SlackClient, SlackError, SlackRateLimited
from connectors.slack.connector import CHANNEL_TYPES


def measure(client: SlackClient, channel: str, calls: int) -> dict:
    """Call conversations.history `calls` times without waiting. Returns what happened."""
    ok, limited, retry_after, sizes = 0, 0, [], []
    started = time.monotonic()
    for _ in range(calls):
        try:
            body = client.call("conversations.history", retry=False, channel=channel, limit=100)
            ok += 1
            sizes.append(len(body.get("messages") or []))
        except SlackRateLimited as exc:
            limited += 1
            retry_after.append(exc.retry_after)
    return {"calls": calls, "ok": ok, "rate_limited": limited, "retry_after_seconds": retry_after,
            "seconds": round(time.monotonic() - started, 2), "messages_per_call": sizes}


def verdict(result: dict) -> str:
    if result["rate_limited"] == 0 and result["ok"] > 1:
        return "NOT rate-limited at this pace: the internal app looks exempt from the 1-request-per-minute cut."
    if result["ok"] <= 1:
        return "Rate-limited after the first call: the 1-request-per-minute cut appears to APPLY. Tell the team."
    return "Some calls were rate-limited: an ordinary tier limit, not the 1-per-minute cut. Polling must stay under it."


def cap_verdict(result: dict) -> str:
    most = max(result["messages_per_call"], default=0)
    if most > 15:
        return f"Up to {most} messages per call: the 15-messages-per-call cap does NOT apply."
    return (f"At most {most} messages per call: not enough to tell whether the 15-messages cap applies. "
            "Use a channel with more than 15 messages.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m connectors.slack.check_rate_limit", description=__doc__.splitlines()[0])
    parser.add_argument("--calls", type=int, default=12)
    parser.add_argument("--channel", help="channel name, without #; default: the bot's channel with the most messages")
    args = parser.parse_args()
    load_dotenv()
    client = SlackClient.from_env()
    try:
        who = client.call("auth.test")
        channels = [c for c in client.pages("conversations.list", "channels", types=CHANNEL_TYPES, limit=200) if c.get("is_member")]
    except SlackError as exc:
        raise SystemExit(f"Slack refused: {exc.code}. Check the token and the app's scopes.") from None
    print(f"workspace: {who.get('team')}   bot user: {who.get('user')}")
    print(f"channels the bot is in ({len(channels)}): " + ", ".join(f"#{c['name']}{' (private)' if c.get('is_private') else ''}"
                                                                    for c in channels))
    if not channels:
        raise SystemExit("The bot is in no channel. Invite it (/invite @<app name>) to every channel it should read.")
    if args.channel:
        channel = next((c for c in channels if c["name"] == args.channel.lstrip("#")), None)
        if channel is None:
            raise SystemExit(f"The bot is not in #{args.channel.lstrip('#')}.")
    else:
        sizes = {c["id"]: len(client.call("conversations.history", channel=c["id"], limit=1000).get("messages") or [])
                 for c in channels}
        channel = max(channels, key=lambda c: sizes[c["id"]])
        print(f"busiest channel: #{channel['name']}, {sizes[channel['id']]} messages")
    result = measure(client, channel["id"], args.calls)
    print(f"conversations.history x{result['calls']} on #{channel['name']}: {result['ok']} ok, {result['rate_limited']} rate-limited, "
          f"{result['seconds']} s")
    if result["retry_after_seconds"]:
        print(f"Retry-After values (s): {result['retry_after_seconds']}")
    print(f"messages returned per call: {result['messages_per_call']}")
    print(verdict(result))
    print(cap_verdict(result))


if __name__ == "__main__":
    main()
