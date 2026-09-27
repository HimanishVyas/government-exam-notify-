#!/usr/bin/env python3
"""
Government exam notification watcher.

Every exam in config/queries.json is searched on Google News RSS. Any exam
with news in the last KEEP_DAYS days is included in the daily email - with
fresh headlines marked NEW - until you mark it Done or Mute.

Marking from your phone: each exam in the email has "Done" and "Mute" links.
Tapping one opens a pre-filled email (subject e.g. "DONE: SSC CGL") to
yourself; just send it. The next run reads it from Gmail over IMAP, applies
it, and archives the command email. "RESUME: <exam>" brings an exam back.

Run manually:  python scripts/check_exams.py
Preview only:  python scripts/check_exams.py --dry-run   (no email, no state change)
Run in CI:     see .github/workflows/daily-check.yml
"""

import email
import html
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, quote_plus

import feedparser

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "queries.json"
STATE_PATH = ROOT / "state" / "seen.json"

MAX_NEW_PER_EXAM = 8        # new headlines taken per exam per run
MAX_ITEM_AGE_DAYS = 30      # ignore articles published longer ago than this
KEEP_DAYS = 45              # keep reminding about an exam this long after its latest news
ITEMS_SHOWN_PER_EXAM = 5
COMMAND_LOOKBACK_DAYS = 14

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"

ACTIVE, DONE, MUTED = "active", "done", "muted"
COMMAND_RE = re.compile(
    r"^\s*(?:(?:re|fwd?)\s*:\s*)*(done|mute|resume|unmute)\s*[:\-]\s*(.+?)\s*$", re.I
)


# ---------------------------------------------------------------- helpers

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_exams():
    """config/queries.json entries are {"name", "query"} objects or plain query strings."""
    exams = []
    for entry in load_json(CONFIG_PATH, []):
        if isinstance(entry, str):
            exams.append({"name": entry, "query": entry})
        elif isinstance(entry, dict) and entry.get("query"):
            exams.append({"name": entry.get("name") or entry["query"], "query": entry["query"]})
    return exams


def parse_iso(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- news

def fetch_query(query):
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))
    feed = feedparser.parse(url)
    if feed.get("bozo") and not feed.entries:
        print(f"Warning: could not fetch '{query}': {feed.get('bozo_exception')}", file=sys.stderr)
    return feed.entries


def collect_news(exams, state, seen_links, seen_order, now):
    """Add unseen, recent headlines to each exam's item list. Returns count of new items."""
    total_new = 0
    for exam in exams:
        record = state["exams"].setdefault(exam["name"], {"status": ACTIVE, "items": []})
        new_items = []
        for entry in fetch_query(exam["query"])[: MAX_NEW_PER_EXAM * 3]:
            link = entry.get("link")
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            seen_order.append(link)

            published = entry.get("published_parsed")
            published_dt = datetime(*published[:6], tzinfo=timezone.utc) if published else now
            if now - published_dt > timedelta(days=MAX_ITEM_AGE_DAYS):
                continue
            source = entry.get("source")
            new_items.append({
                "title": entry.get("title", "(no title)"),
                "link": link,
                "source": source.get("title", "") if source else "",
                "published": published_dt.isoformat(),
                "first_seen": now.isoformat(),
            })
            if len(new_items) >= MAX_NEW_PER_EXAM:
                break

        # Newest first; drop items past the reminder window.
        cutoff = now - timedelta(days=KEEP_DAYS)
        items = new_items + record["items"]
        record["items"] = [
            i for i in items if (parse_iso(i.get("published")) or now) >= cutoff
        ][:20]
        total_new += len(new_items)
    return total_new


# ---------------------------------------------------------------- commands (Done / Mute / Resume)

def _decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def fetch_commands(user, password, allowed_senders):
    """Read DONE/MUTE/RESUME emails from Gmail. Returns (imap connection, [commands])."""
    host = os.environ.get("IMAP_HOST", "imap.gmail.com")
    conn = imaplib.IMAP4_SSL(host)
    conn.login(user, password)
    conn.select("INBOX")
    since = (datetime.now(timezone.utc) - timedelta(days=COMMAND_LOOKBACK_DAYS)).strftime("%d-%b-%Y")

    commands, uids = [], set()
    for sender in allowed_senders:
        _, data = conn.uid("search", None, "SINCE", since, "FROM", f'"{sender}"')
        uids.update(data[0].split())

    for uid in sorted(uids, key=int):
        _, data = conn.uid(
            "fetch", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])"
        )
        raw = next((part[1] for part in data if isinstance(part, tuple)), None)
        if not raw:
            continue
        msg = email.message_from_bytes(raw)
        sender = parseaddr(msg.get("From", ""))[1].lower()
        match = COMMAND_RE.match(_decode(msg.get("Subject")))
        if sender not in allowed_senders or not match:
            continue
        try:
            sent_at = parsedate_to_datetime(msg.get("Date"))
        except (TypeError, ValueError):
            sent_at = None
        commands.append({
            "uid": uid,
            "message_id": (msg.get("Message-ID") or f"uid-{uid.decode()}").strip(),
            "action": match.group(1).lower(),
            "target": match.group(2),
            "sent_at": sent_at.timestamp() if sent_at else 0,
        })
    commands.sort(key=lambda c: c["sent_at"])
    return conn, commands


def resolve_exam(target, names):
    target_l = target.strip().lower()
    for name in names:
        if name.lower() == target_l:
            return name
    partial = [n for n in names if target_l in n.lower()]
    return partial[0] if len(partial) == 1 else None


def apply_commands(commands, state, names):
    processed = set(state.get("processed_commands", []))
    notes = []
    for cmd in commands:
        if cmd["message_id"] in processed:
            continue
        processed.add(cmd["message_id"])
        state.setdefault("processed_commands", []).append(cmd["message_id"])
        name = resolve_exam(cmd["target"], names)
        if not name:
            notes.append(f'Could not find an exam called "{cmd["target"]}" - check the spelling.')
            continue
        record = state["exams"].setdefault(name, {"status": ACTIVE, "items": []})
        new_status = {"done": DONE, "mute": MUTED}.get(cmd["action"], ACTIVE)
        record["status"] = new_status
        record["status_changed"] = datetime.now(timezone.utc).isoformat()
        label = {DONE: "Marked done", MUTED: "Muted", ACTIVE: "Resumed"}[new_status]
        notes.append(f"{label}: {name}")
    state["processed_commands"] = state.get("processed_commands", [])[-500:]
    return notes


def archive_commands(conn, commands):
    """Remove processed command emails from the inbox (Gmail archives them)."""
    if not commands:
        return
    for cmd in commands:
        conn.uid("store", cmd["uid"], "+FLAGS", "(\\Deleted)")
    conn.expunge()


# ---------------------------------------------------------------- email

def command_link(address, action, name):
    return f"mailto:{address}?subject={quote(f'{action}: {name}')}&body={quote('Just press send.')}"


def fmt_date(iso):
    dt = parse_iso(iso)
    return dt.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b") if dt else ""


def render_email(exams, state, run_started, command_address, notes):
    """Build (html, text, counts) for every active exam that has recent news."""
    esc = html.escape
    active_with_news, quiet, parked = [], [], []
    for exam in exams:
        record = state["exams"].get(exam["name"], {"status": ACTIVE, "items": []})
        if record["status"] != ACTIVE:
            parked.append((exam["name"], record["status"]))
        elif record["items"]:
            new_count = sum(1 for i in record["items"] if i.get("first_seen") >= run_started)
            active_with_news.append((exam["name"], record["items"], new_count))
        else:
            quiet.append(exam["name"])
    # Exams with fresh headlines first.
    active_with_news.sort(key=lambda x: -x[2])

    h = ['<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.45;max-width:640px">']
    t = []

    if notes:
        h.append('<div style="background:#eef6ee;padding:8px 12px;border-radius:6px;margin-bottom:12px">'
                 + "<br>".join(esc(n) for n in notes) + "</div>")
        t += notes + [""]

    h.append(f"<h2 style='margin:0 0 4px'>Exams to review: {len(active_with_news)}</h2>"
             "<p style='margin:0 0 16px;color:#555'>Each stays here daily until you tap "
             "<b>Done</b> or <b>Mute</b> (opens a pre-filled email - just send it).</p>")
    t.append(f"Exams to review: {len(active_with_news)}")
    t.append('To stop reminders for an exam, email yourself the subject "DONE: <exam>" or "MUTE: <exam>".')
    t.append("")

    for name, items, new_count in active_with_news:
        badge = (f" <span style='background:#d33;color:#fff;border-radius:4px;padding:1px 6px;"
                 f"font-size:12px'>{new_count} NEW</span>") if new_count else ""
        h.append(f"<h3 style='margin:18px 0 4px'>{esc(name)}{badge}</h3>")
        h.append(
            f"<div style='margin-bottom:6px'>"
            f"<a href='{esc(command_link(command_address, 'DONE', name))}' "
            f"style='background:#2a7;color:#fff;padding:4px 10px;border-radius:4px;text-decoration:none'>Done</a> "
            f"<a href='{esc(command_link(command_address, 'MUTE', name))}' "
            f"style='background:#888;color:#fff;padding:4px 10px;border-radius:4px;text-decoration:none'>Mute</a>"
            f"</div><ul style='margin-top:4px;padding-left:20px'>"
        )
        t.append(f"## {name}" + (f"  [{new_count} NEW]" if new_count else ""))
        for item in items[:ITEMS_SHOWN_PER_EXAM]:
            is_new = item.get("first_seen") >= run_started
            tag = "<b style='color:#d33'>NEW</b> " if is_new else ""
            meta = " &middot; ".join(filter(None, [esc(item.get("source", "")), fmt_date(item.get("published"))]))
            h.append(f"<li>{tag}<a href='{esc(item['link'])}'>{esc(item['title'])}</a>"
                     f"<br><small style='color:#777'>{meta}</small></li>")
            t.append(f"- {'NEW ' if is_new else ''}{item['title']}\n  {item['link']}")
        h.append("</ul>")
        t.append("")

    if quiet:
        h.append("<p style='color:#777'><b>No recent news:</b> " + esc(", ".join(quiet)) + "</p>")
        t.append("No recent news: " + ", ".join(quiet))
    if parked:
        links = ", ".join(
            f"{esc(n)} ({s}, <a href='{esc(command_link(command_address, 'RESUME', n))}'>resume</a>)"
            for n, s in parked
        )
        h.append(f"<p style='color:#777'><b>Stopped:</b> {links}</p>")
        t.append("Stopped: " + ", ".join(f"{n} ({s})" for n, s in parked))

    h.append("<p style='color:#999;font-size:12px'>News-based alerts - always confirm dates on the "
             "official exam website.</p></div>")

    total_new = sum(n for _, _, n in active_with_news)
    return "\n".join(h), "\n".join(t), len(active_with_news), total_new


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
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=context) as server:
        server.login(user, password)
        server.sendmail(user, to_addr, msg.as_string())


# ---------------------------------------------------------------- main

def main():
    dry_run = "--dry-run" in sys.argv[1:]
    now = datetime.now(timezone.utc)
    run_started = now.isoformat()

    exams = load_exams()
    if not exams:
        print("No exams configured in config/queries.json - nothing to do.")
        return
    names = [e["name"] for e in exams]

    state = load_json(STATE_PATH, {})
    state.setdefault("exams", {})
    seen_order = list(state.get("seen_links", []))
    seen_links = set(seen_order)

    user = os.environ.get("EMAIL_ADDRESS", "")
    password = os.environ.get("EMAIL_PASSWORD", "")
    to_addr = os.environ.get("EMAIL_TO") or user

    # 1. Apply Done/Mute/Resume emails sent since the last run.
    imap_conn, commands, notes = None, [], []
    if user and password:
        try:
            imap_conn, commands = fetch_commands(
                user, password, {user.lower(), to_addr.lower()}
            )
            notes = apply_commands(commands, state, names)
        except Exception as e:
            print(f"Warning: could not read Done/Mute emails: {e}", file=sys.stderr)
    for note in notes:
        print(note)

    # 2. Pull news.
    total_new = collect_news(exams, state, seen_links, seen_order, now)

    # 3. Email every active exam with recent news.
    html_body, text_body, n_exams, n_new = render_email(exams, state, run_started, user or to_addr, notes)
    subject = (f"Govt exams: {n_new} new, {n_exams} to review - "
               f"{now.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b')}")
    print(f"{total_new} new headline(s); {n_exams} exam(s) to review.")

    if dry_run:
        print(f"[dry run] Subject: {subject}\n")
        print(text_body)
        return

    if n_exams or notes:
        try:
            send_email(subject, html_body, text_body)
            print("Email sent.")
        except KeyError as e:
            print(f"Missing required environment variable/secret: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Failed to send email: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Nothing to email.")

    # 4. Save state, then archive the command emails we applied.
    state["seen_links"] = seen_order[-5000:]
    state["last_run_utc"] = run_started
    save_json(STATE_PATH, state)

    if imap_conn:
        try:
            archive_commands(imap_conn, commands)
            imap_conn.logout()
        except Exception as e:
            print(f"Warning: could not archive command emails: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
