#!/usr/bin/env python3
"""
Government exam notification watcher.

Polls Google News RSS for a configurable list of search queries, keeps
track of links already seen in state/seen.json, and emails a digest of
anything new.

Run manually:  python scripts/check_exams.py
Preview only:  python scripts/check_exams.py --dry-run   (no email, no state change)
Run in CI:     see .github/workflows/daily-check.yml
"""

import html
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus

import feedparser

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "queries.json"
STATE_PATH = ROOT / "state" / "seen.json"
MAX_ITEMS_PER_QUERY = 8
MAX_AGE_DAYS_FIRST_RUN = 14

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_query(query):
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))
    feed = feedparser.parse(url)
    return feed.entries


def build_digest(queries, seen_links, seen_order, first_run):
    new_by_query = {}
    now = datetime.now(timezone.utc)

    for query in queries:
        entries = fetch_query(query)
        fresh = []
        for entry in entries[: MAX_ITEMS_PER_QUERY * 3]:
            link = entry.get("link")
            if not link or link in seen_links:
                continue
            if first_run:
                published = entry.get("published_parsed")
                if published:
                    published_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if (now - published_dt).days > MAX_AGE_DAYS_FIRST_RUN:
                        seen_links.add(link)
                        seen_order.append(link)
                        continue
            fresh.append(entry)
            seen_links.add(link)
            seen_order.append(link)
            if len(fresh) >= MAX_ITEMS_PER_QUERY:
                break
        if fresh:
            new_by_query[query] = fresh

    return new_by_query


def render_email(new_by_query):
    lines_html = ["<h2>New government exam notifications</h2>"]
    lines_text = ["New government exam notifications", ""]

    for query, entries in new_by_query.items():
        lines_html.append(f"<h3>{html.escape(query)}</h3><ul>")
        lines_text.append(f"## {query}")
        for e in entries:
            title = e.get("title", "(no title)")
            link = e.get("link", "")
            source = e.get("source", {}).get("title", "") if e.get("source") else ""
            published = e.get("published", "")
            lines_html.append(
                f'<li><a href="{html.escape(link)}">{html.escape(title)}</a>'
                f"<br><small>{html.escape(source)} &middot; {html.escape(published)}</small></li>"
            )
            lines_text.append(f"- {title}\n  {link}\n  {source} - {published}")
        lines_html.append("</ul>")
        lines_text.append("")

    return "\n".join(lines_html), "\n".join(lines_text)


def send_email(subject, html_body, text_body):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["EMAIL_ADDRESS"]
    password = os.environ["EMAIL_PASSWORD"]
    to_addr = os.environ.get("EMAIL_TO") or user

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context) as server:
        server.login(user, password)
        server.sendmail(user, to_addr, msg.as_string())


def main():
    dry_run = "--dry-run" in sys.argv[1:]

    queries = load_json(CONFIG_PATH, [])
    if not queries:
        print("No queries configured in config/queries.json - nothing to do.")
        return

    state = load_json(STATE_PATH, {"seen_links": []})
    seen_order = list(state.get("seen_links", []))
    seen_links = set(seen_order)
    first_run = len(seen_links) == 0

    new_by_query = build_digest(queries, seen_links, seen_order, first_run)

    if not dry_run:
        # Keep the most recently seen links (list preserves insertion order).
        state["seen_links"] = seen_order[-5000:]
        state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        save_json(STATE_PATH, state)

    if not new_by_query:
        print("No new items found.")
        return

    total = sum(len(v) for v in new_by_query.values())
    print(f"Found {total} new item(s) across {len(new_by_query)} quer(y/ies). Emailing digest...")

    html_body, text_body = render_email(new_by_query)
    subject = f"New govt exam notification(s) - {datetime.now().strftime('%d %b %Y')} ({total})"

    if dry_run:
        print(f"[dry run] Subject: {subject}\n")
        print(text_body)
        return

    try:
        send_email(subject, html_body, text_body)
        print("Email sent.")
    except KeyError as e:
        print(f"Missing required environment variable/secret: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Failed to send email: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
