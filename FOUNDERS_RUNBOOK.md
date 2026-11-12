# Founder emails from connect@ (weekdays)

`founders.py` decides who gets what; the scheduled Claude session only carries the emails to Zoho through Composio
and writes back what happened. Never change the email text or recipients by hand. Never send these through Resend.

- Sender: **connect@coupondonation.com** — Composio account `zoho_mail_termor-medial`, accountId `3837587000000002002`, region `in`.
- Folders: Inbox `3837587000000002014`, Notification `3837587000000003001`, Sent `3837587000000002022`.
- Who: `founders_queue.json` (from the donor prospects sheet; set `"status": "Skip"` to leave someone out).
- Wording: `founders_text.json` (approved by the founder on Nov 12, 2026).
- Limits (config.json): `founders_per_day` first emails per weekday (15), one follow-up in the same thread after
  `founders_followup_days` (5) with no reply. 3 bounces in a day pause sending (`founders_state.json` → `paused`).
- Off switch: `"founders_enabled": false` or `"founders_paused": "<reason>"` in config.json.

## 0. Repo
```
cd /home/claude/outreach 2>/dev/null && git pull -q origin main || git clone https://github.com/Harshit150796/outreach /home/claude/outreach
cd /home/claude/outreach && git config user.name "outreach-bot" && git config user.email "outreach-bot@users.noreply.github.com"
```

## 1. Read the connect@ inbox
Two `ZOHO_MAIL_MESSAGES_LIST_EMAILS` calls on `zoho_mail_termor-medial`: `account_id 3837587000000002002`, `region in`,
`limit 100`, `sort_by date`, `sort_order false`, once with `folder_id 3837587000000002014` (Inbox) and once with
`3837587000000003001` (Notification). Keep messages received in the last 3 days, write
`{"from": fromAddress, "subject": subject, "text": summary}` for each into `/tmp/f_inbox.json` (a JSON list, `[]` is fine),
then `python3 founders.py inbox --file /tmp/f_inbox.json`.

## 1b. Only if `python3 founders.py status` shows pending > 0
For each pending address (`python3 -c "import json;print([v['to'] for v in json.load(open('founders_state.json'))['pending'].values()])"`)
run `ZOHO_MAIL_SEARCH_MESSAGES` on `zoho_mail_termor-medial`, `account_id 3837587000000002002`, `region in`,
`search_key "to:<address>"`. Write hits dated on/after the pending date as `{"<address>": {"message_id": "..."}}` to
`/tmp/f_found.json`, then `python3 founders.py reconcile --found /tmp/f_found.json`. If a search errors, stop and report.

## 2. Plan
`python3 founders.py plan --out /tmp/f_plan.json`. If it prints `"items": 0`, report the notes and stop (commit + push
founders_state.json first if step 1 changed it). Otherwise commit and push **before sending**:
`git add founders_state.json founders_last_plan.json && git commit -qm "Founder plan $(date -u +%F)" && git pull -q --rebase origin main && git push -q`.

## 3. Send, 5 at a time
`python3 founders.py batch --plan /tmp/f_plan.json --start 0 --n 5` prints the exact `tools` array (keys on stderr).
Pass it unchanged to `COMPOSIO_MULTI_EXECUTE_TOOL`. Repeat with `--start 5`, `--start 10`, … Note per key: success and
`data.data.messageId`. Stop sending on an auth, rate/limit/quota error, or more than 1 failure in a batch.

## 4. Record
Write `/tmp/f_results.json` as `{"<key>": {"ok": true, "message_id": "..."}, "<key>": {"ok": false, "error": "..."}}` for
every key attempted (leave out keys never sent). `python3 founders.py record --plan /tmp/f_plan.json --results /tmp/f_results.json`,
`python3 founders.py status`, then commit and push founders_state.json (pull --rebase first, retry 3 times).

If a send call is refused by a permission/safety block (not a Zoho error), don't retry or reword it: leave the emails
pending, commit + push, and put the block in the first line of the report.

## 5. Report
Push one notification: sent (first / follow-up), failures, new replies (address and subject; these are founders
to answer personally from connect@), opt-outs, bounces, any pause, and how many founders are still queued.
