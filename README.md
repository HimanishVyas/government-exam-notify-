# Govt Exam Notifier

Every morning (~9:00 IST) GitHub Actions searches Google News for each
exam in `config/queries.json` and emails you one digest.

- Any exam with news in the last 45 days is in the email **every day**,
  with fresh headlines marked **NEW**.
- It stays there until you tap **Done** or **Mute** under that exam in the
  email. That opens a pre-filled email to yourself - just press send. The
  next morning's run picks it up, stops that exam, and archives your
  command email.
- Stopped exams are listed at the bottom with a **resume** link.

You can also type the command yourself: send an email to yourself with the
subject `DONE: SSC CGL`, `MUTE: GSSSB` or `RESUME: SSC CGL` (case doesn't
matter; part of the name is fine if it's unique).

This is news-based - always confirm dates on the official exam website.

## One-time setup (needed before emails work)

1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Create an App Password: https://myaccount.google.com/apppasswords
   (name it "github-actions") and copy the 16-character password.
3. In this repo: Settings -> Secrets and variables -> Actions -> New
   repository secret. Add `EMAIL_ADDRESS` (your Gmail), `EMAIL_PASSWORD`
   (the app password) and `EMAIL_TO` (where digests go - can be the same
   Gmail).
4. Actions tab -> "Daily govt exam notification check" -> Run workflow.

The same app password is used to send the digest (SMTP) and to read your
Done/Mute emails (IMAP). Only emails sent from `EMAIL_ADDRESS` or
`EMAIL_TO` are obeyed.

## Adding or removing exams

Edit `config/queries.json`: each entry has a `name` (shown in the email and
used for Done/Mute) and a Google News `query`.

## Website (dashboard)

Every run also publishes a phone-friendly page with all exams, headlines
and Done / Mute / Resume buttons:
`https://himanishvyas.github.io/government-exam-notify-/`

To turn it on (one time): the repo must be public (or on GitHub Pro), then
Settings -> Pages -> Source: **GitHub Actions**, and run the workflow once.
Your email address is never put on the page - the first time you tap Done
or Mute it asks for it and remembers it on your phone only.
