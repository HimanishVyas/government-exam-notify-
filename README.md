# Govt Exam Notifier

Watches for new government job notifications (Gujarat + national, any
degree and engineering-specific) and emails a digest — runs free on
GitHub Actions once a day, no server needed.

How it works: each phrase in config/queries.json is checked against
Google News RSS (same mechanism as Google Alerts). New items since the
last run are emailed; seen links are remembered in state/seen.json.

This is a personal notifier, not an official feed - confirm anything
important on the exam body's own website before acting on it.

## Setup

1. Turn on 2-Step Verification on the Gmail account you'll send from:
   https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
   (App: Mail, Device: Other -> name it "github-actions"). Copy the
   16-character password.
3. In this GitHub repo: Settings -> Secrets and variables -> Actions ->
   New repository secret. Add:
   - EMAIL_ADDRESS  = the Gmail address from step 2
   - EMAIL_PASSWORD = the 16-character app password
   - EMAIL_TO       = the address you want the digest sent to
4. Go to the Actions tab -> enable the workflow if prompted -> click
   "Run workflow" once to test.
5. Check the run log for "Email sent", check your inbox (and spam), and
   confirm state/seen.json got a commit after the run.

First run note: state/seen.json starts empty, so the first email will
include everything found in the last ~14 days - expect a bigger first
digest. After that you'll only get genuinely new items.

## Customizing

- Search terms: edit config/queries.json. Keep phrases specific.
- Schedule: edit the cron line in .github/workflows/daily-check.yml
  (UTC time; use crontab.guru to build a new expression).
- Items per query per email: MAX_ITEMS_PER_QUERY in
  scripts/check_exams.py.

## Limitations

- Relies on news coverage of a notification, not the notification
  itself - not a substitute for checking official portals directly for
  anything with a hard deadline.
- Google News RSS can occasionally rate-limit or change format.
- Does not modify, apply to, or interact with any exam portal - it only
  reads public news search results and emails a summary.
