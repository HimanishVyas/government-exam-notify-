#!/usr/bin/env python3
"""
Build the dashboard data. Writes a slim data.json from config + state to
the repo root (committed, so Pages "Deploy from a branch" serves it next to
index.html) and assembles _site/ for the "GitHub Actions" Pages source.

Run locally:  python scripts/build_site.py && python -m http.server -d _site
"""

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from check_exams import ACTIVE, KEEP_DAYS, ROOT, STATE_PATH, load_exams, load_json, parse_iso

OUT = ROOT / "_site"


def build():
    state = load_json(STATE_PATH, {})
    last_run = state.get("last_run_utc")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=KEEP_DAYS)

    exams = []
    for exam in load_exams():
        record = state.get("exams", {}).get(exam["name"], {})
        items = [
            {
                "title": i["title"],
                "link": i["link"],
                "source": i.get("source", ""),
                "published": i.get("published"),
                "new": bool(last_run) and i.get("first_seen", "") >= last_run,
            }
            for i in record.get("items", [])
            if (parse_iso(i.get("published")) or now) >= cutoff
        ]
        items.sort(key=lambda i: i["published"] or "", reverse=True)
        items = items[:10]
        exams.append({"name": exam["name"], "status": record.get("status", ACTIVE), "items": items})

    payload = json.dumps({"last_run_utc": last_run, "exams": exams}, ensure_ascii=False)
    (ROOT / "data.json").write_text(payload, encoding="utf-8")

    OUT.mkdir(exist_ok=True)
    (OUT / "data.json").write_text(payload, encoding="utf-8")
    shutil.copy(ROOT / "index.html", OUT / "index.html")
    print(f"Built {OUT} with {len(exams)} exams.")


if __name__ == "__main__":
    build()
