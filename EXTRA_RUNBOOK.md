# Gmail + Zoho daily run (weekdays)

The scheduled Claude session follows these steps every weekday. `extra.py` decides
*who* gets *what*; the session only carries the emails to Gmail/Zoho through Composio and
writes back what happened. Never change the email text or recipients by hand.

Senders:
- **Gmail** `connect.coupondonation@gmail.com` (Composio account `gmail_spitz-fixed`): first emails to new
  churches (ramp 10 → 15 → 20 → 30 a day), plus one follow-up in the same thread 4+ days later.
- **Zoho** `hello@coupondonation.com` (Composio account `zoho_mail_ungot-snail`, accountId 5347274000000002002,
  region `in`): one follow-up to churches that got the Resend email 4+ days ago (ramp 10 → 20 → 25 a day).
  No cold first emails from Zoho.
- Resend keeps sending first emails from GitHub Actions at 12:41 UTC. It skips every church in extra_state.json.

## 0. Repo
```
cd /home/claude/outreach 2>/dev/null && git pull -q origin main || git clone https://github.com/Harshit150796/outreach /home/claude/outreach
cd /home/claude/outreach && git config user.name "outreach-bot" && git config user.email "outreach-bot@users.noreply.github.com"
```
(If the repo is not attached to the session, attach Harshit150796/outreach with push access first.)

Then check that today's Resend job ran (GitHub sometimes skips scheduled runs):
`gh api 'repos/Harshit150796/outreach/actions/workflows/daily-outreach.yml/runs?per_page=1' --jq '.workflow_runs[0].created_at'`.
If that time is before 12:00 UTC today (or there is no run), start it once:
`gh api -X POST repos/Harshit150796/outreach/actions/workflows/daily-outreach.yml/dispatches -f ref=main`
and say so in the report. Don't start it if a run already exists today, whatever its result.

## 1. Read both inboxes (replies, opt-outs, bounces)
- Gmail: `GMAIL_FETCH_EMAILS` on `gmail_spitz-fixed` with
  `query: 'newer_than:3d in:inbox (subject:re OR subject:"following up" OR from:mailer-daemon OR from:postmaster OR unsubscribe OR "delivery status")'`,
  `max_results: 100`, `verbose: false`. Write every message as `{"from": sender, "subject": subject, "text": preview.body}`
  into `/tmp/gmail_inbox.json` (a JSON list), then `python3 extra.py inbox --source gmail --file /tmp/gmail_inbox.json`.
- Zoho: `ZOHO_MAIL_MESSAGES_LIST_EMAILS` on `zoho_mail_ungot-snail` with `account_id 5347274000000002002`, `region in`,
  `folder_id 5347274000000002008` (Inbox), `limit 100`, `sort_by date`, `sort_order false`. Keep messages received in the
  last 3 days, write `{"from": fromAddress, "subject": subject, "text": summary}` into `/tmp/zoho_inbox.json`, then
  `python3 extra.py inbox --source zoho --file /tmp/zoho_inbox.json`.
Copy the fields exactly; empty list `[]` is fine. Replies stop follow-ups; "unsubscribe" wording suppresses the address;
bounces suppress it and count toward that sender's bounce limit (over 5% in 14 days pauses that sender).

## 2. Plan
`python3 extra.py plan --out /tmp/plan.json`. If it prints `"items": 0`, report its notes and stop
(commit + push extra_state.json first if step 1 changed it).

The plan reserves its emails as *pending*. **Commit and push extra_state.json now, before sending**, so a crash can never
lead to a second copy: `git add extra_state.json && git commit -qm "Gmail/Zoho plan $(date -u +%F)" && git pull -q --rebase origin main && git push -q`.

## 3. Send, 10 at a time
`python3 extra.py batch --plan /tmp/plan.json --start 0 --n 10` prints the exact `tools` array (and the keys, in order,
on stderr). Pass it unchanged to `COMPOSIO_MULTI_EXECUTE_TOOL`. Repeat with `--start 10`, `--start 20`, … until done.
After each call note, per key: success, the message id and the Gmail threadId:
- Gmail send / reply: `data.id` = message id, `data.threadId` = thread id.
- Zoho send: `data.data.messageId`.
Stop sending (record what you have) if a batch returns an auth error, a rate/limit/quota error, or more than 2 failures.

## 4. Record
Write `/tmp/results.json` as `{"<key>": {"ok": true, "message_id": "...", "thread_id": "..."}, "<key>": {"ok": false, "error": "..."}}`
for every key you attempted (leave out keys you never sent, they stay pending and block the next plan until checked).
`python3 extra.py record --plan /tmp/plan.json --results /tmp/results.json`, then
`python3 extra.py status`, then commit and push extra_state.json (pull --rebase before push, retry 3 times).

Pushing the recorded extra_state.json starts the **Call list email** GitHub workflow, which emails
connect.coupondonation@gmail.com every church just emailed (phone, pastor, city, map link). Don't send it yourself.

## 5. Report
One short message: sent counts per sender, failures, new replies (who, subject) and opt-outs, any pause.
Replies are real people: list them so the founder can answer from the Gmail / Zoho inbox.

## Pause / resume
- Everything: set `"extra_paused": true` in config.json.
- One sender: `extra_state.json` → `"paused": {"gmail": "reason"}`; delete the entry to resume.
- Stuck pending after a crash: look in the Gmail/Zoho Sent folder; record what was sent, then `python3 extra.py clear-pending`.
