"""The daily dispatch: one honest post about how the agent is doing, ready for X.

Build in public means the losses ship too. This assembles a short status update
straight from the journal — the same source the public feed reads, never a fresh
mark-to-market — so a stale-but-true number never becomes a fresh-but-invented
one. It is templated, not written by a model: the account this speaks for should
be able to post every day at zero token cost, and a deterministic summary can't
hallucinate a profit we didn't make.

`daily_text` returns the draft; `post_to_x` publishes it. Nothing is sent unless
a caller asks — posting is outward-facing and irreversible, so it is opt-in, gated
on credentials that live in the environment and never in the repo.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from ..config import Settings
from ..memory.journal import Journal

SITE = "openthomas.com"
LIMIT = 280  # X's per-post character ceiling


def _money(v: float) -> str:
    """Signed, whole-dollar, ASCII — the sign is the news on a P&L line."""
    return f"{'+' if v >= 0 else '-'}${abs(v):,.0f}"


def _today(journal: Journal, day: str) -> list[dict]:
    return [s for s in journal.recent_settlements(limit=200) if s["ts"][:10] == day]


def daily_text(journal: Journal, settings: Settings, day: str | None = None) -> str:
    """The build-in-public post for `day` (UTC date, defaults to today).

    Headline and link are always kept; the day's activity and the standing call
    are added only while they fit under the character limit, longest-lived facts
    first. Everything is drawn from settled, recorded state — no claim here that
    isn't already on the page the link points to.
    """
    from ..site.feed import _theses  # local import: avoids a feed→dispatch cycle

    day = day or datetime.now(timezone.utc).date().isoformat()
    curve = journal.equity_curve()
    value = curve[-1][1] if curve else settings.bankroll
    ret = value / settings.bankroll - 1 if settings.bankroll else 0.0
    stats = journal.settlement_stats()

    head = f"OpenThomas · {settings.mode} weather-trading agent"
    value_line = f"${value:,.0f} book, {ret:+.2%} since ${settings.bankroll:,.0f} start."
    link = f"{SITE} — every claim timestamped before it settles"

    optional: list[str] = []
    today = _today(journal, day)
    if today:
        line = f"Today: {len(today)} settled, {_money(sum(s['pnl'] for s in today))}."
        if stats["n"]:
            line += f" {stats['win_rate']:.0%} win over {stats['n']}."
        optional.append(line)
    else:
        held = len(journal.positions())
        optional.append(f"No settlements today; holding {held} position"
                        f"{'' if held == 1 else 's'}, watching the board.")

    theses = _theses(journal, settings)
    if theses:
        t = theses[0]
        # Prefer the place (a city says where the weather is); fall back to a short
        # question snippet for global markets a station lookup can't pin.
        subject = (t.get("loc") or {}).get("place") or t["question"].rstrip("? ")[:34]
        optional.append(f"Widest call: {subject} {t['side']} — we say "
                        f"{t['p_model'] * 100:.0f}¢ vs market {t['p_market'] * 100:.0f}¢.")

    body = [head, value_line]
    for line in optional:  # add each only if the whole post still fits
        if len("\n".join([*body, line, link])) <= LIMIT:
            body.append(line)
    return "\n".join([*body, link])


def _x_credentials() -> dict[str, str] | None:
    """OAuth 1.0a user-context keys from the environment, or None if incomplete.

    Kept out of the repo and out of config.yaml on purpose: a key that can post
    as the account is exactly what OSS hygiene says never ships. Set them in the
    process env (e.g. ~/.openthomas/env, sourced by the runner)."""
    keys = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
    vals = {k: os.environ.get(k, "") for k in keys}
    return vals if all(vals.values()) else None


def post_to_x(text: str, settings: Settings | None = None) -> str:
    """Publish `text` to X, returning the post URL. Raises if it cannot.

    Uses tweepy (an optional dependency) with credentials from the environment.
    A missing library or missing keys is a setup problem, not a runtime one, so
    it fails loudly with the fix rather than silently dropping the post.
    """
    creds = _x_credentials()
    if creds is None:
        raise RuntimeError(
            "X credentials not set. Export X_API_KEY / X_API_SECRET / "
            "X_ACCESS_TOKEN / X_ACCESS_SECRET (a write-enabled app) before --to-x."
        )
    try:
        import tweepy  # optional: `pip install tweepy`
    except ImportError as e:
        raise RuntimeError("Posting to X needs tweepy — `pip install tweepy`.") from e

    client = tweepy.Client(
        consumer_key=creds["X_API_KEY"], consumer_secret=creds["X_API_SECRET"],
        access_token=creds["X_ACCESS_TOKEN"], access_token_secret=creds["X_ACCESS_SECRET"],
    )
    resp = client.create_tweet(text=text)
    tweet_id = resp.data["id"]
    handle = (settings.site.x_handle if settings else "") or "i"
    return f"https://x.com/{handle}/status/{tweet_id}"
