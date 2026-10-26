# Daily church outreach on GitHub Actions

Runs every day at 12:41 UTC (about 8:41am New York). Each run checks the config gate,
the bounce and complaint rates (it pauses itself above 4% / 0.1%), opt-outs, and duplicates,
then sends today's batch with run.py and commits state.json back to the repo.
If any check can't finish, nothing is sent and the run fails. GitHub emails you about failed runs.

## One-time setup (about 5 minutes)
1. Create a new **private** repo on github.com and upload all of these files, including the `.github` folder.
2. In Resend (API Keys), create two keys:
   - a **Sending access** key for outreach.coupondonation.com. Replace the old key, because it was exposed in a prompt.
   - a **Full access** key, used only to read sent and received mail for the checks.
3. In the repo, go to Settings → Secrets and variables → Actions → New repository secret:
   - `RESEND_SEND_KEY` = the sending key
   - `RESEND_FULL_KEY` = the full-access key
4. Go to Actions → "Daily church outreach" → **Run workflow** to send today's batch now and confirm it works.

## Day to day
- Pause: set `"paused": true` in config.json, either on github.com or locally.
- See results: the Actions tab. Each run has a short summary.
- state.json in the repo now records what has been sent. The old Claude artifact stops updating.
