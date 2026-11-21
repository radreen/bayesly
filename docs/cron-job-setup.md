# Reliable 15-minute ticks with cron-job.org (free)

GitHub's own `schedule:` trigger is best-effort: under load it delays or drops runs (we saw only
2 runs on some days). cron-job.org calls GitHub's API on time and starts the same workflow. The
GitHub schedule stays on as a backup; the `concurrency` group makes sure two runs never overlap.

Cost: free. Nothing is posted by this setup itself; it only starts the usual `check`.

## 1. A GitHub token that can only start workflows (owner does this)
1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** →
   Generate new token.
2. Name: `bayesly-cron`. Expiration: 1 year (put a reminder in your calendar).
3. Repository access: **Only select repositories** → `radreen/bayesly`.
4. Permissions → Repository permissions → **Actions: Read and write**. Nothing else.
5. Generate, copy the token (starts with `github_pat_`). Don't paste it into chat or into the repo.

## 2. The cron job (owner does this)
1. Sign up at https://cron-job.org (free) and confirm the email.
2. Dashboard → **Create cronjob**:
   - Title: `bayesly check`
   - URL: `https://api.github.com/repos/radreen/bayesly/actions/workflows/autopost.yml/dispatches`
   - Execution schedule: **Every 15 minutes**
3. Tab **Advanced**:
   - Request method: **POST**
   - Headers (add 3):
     - `Authorization` = `Bearer github_pat_...` (your token from step 1)
     - `Accept` = `application/vnd.github+json`
     - `X-GitHub-Api-Version` = `2022-11-28`
   - Request body: `{"ref":"main","inputs":{"command":"check"}}`
   - Treat as success: HTTP **204** (GitHub answers 204 No Content when the run starts).
4. Save → **Test run**. Expected: status 204. Then GitHub → Actions → a new `autopost` run
   "workflow_dispatch" appears within a few seconds.

## Notes
- Live or dry-run is still decided by the repo variable `BAYESLY_LIVE`; this job doesn't change it.
- The kill switch (`/pause` in Telegram, `BAYESLY_PAUSED=1`) works the same for these runs.
- If cron-job.org shows 401/403: token expired or lacks "Actions: Read and write". 404: wrong repo
  or workflow file name. 422: wrong `ref` or inputs JSON.
- To stop: disable the job on cron-job.org (GitHub's schedule keeps running as before).
