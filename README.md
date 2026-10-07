# Nara Bid Monitor

나라장터 입찰 공고를 조회하고, 개발 관련 공고를 필터링해 Telegram으로 알림을 보내는 Python 스크립트입니다.

## Local Setup

```powershell
cd C:\Users\user\PERSONAL\dev\bid_monitor
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`.env`에는 실제 값을 넣습니다. `.env`는 Git에 커밋하지 않습니다.

```env
DATA_GO_KR_SERVICE_KEY=your_data_go_kr_service_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
CHECK_DAYS=3
SEND_TELEGRAM=true
SEND_EMPTY_SUMMARY=false
DOCUMENT_TIMEOUT=8
MAX_DOCUMENT_DOWNLOADS=3
MOCK_MODE=false
SQLITE_PATH=data/bids.sqlite
```

## Run

```powershell
python main.py
```

Useful checks:

```powershell
python -m compileall .
python main.py --test-nara
python main.py --test-telegram
python main.py --list-matched
python main.py --diagnose-nara
```

`--test-telegram` and `--force-notify-test` send a real Telegram message when
`SEND_TELEGRAM=true`. Use them only for an explicitly approved production
smoke test. They are not run by the automated test suite or routine validation.

## GitHub Actions

Workflow file:

```text
.github/workflows/bid-monitor.yml
```

The workflow:

- runs manually with `workflow_dispatch`
- runs every day at `00:17`, `03:17`, `06:17`, and `09:17 UTC`
  (`09:17`, `12:17`, `15:17`, and `18:17 KST`)
- uses Python 3.11
- installs dependencies from `requirements.txt`
- runs `python main.py`
- commits `state/sent_bids.json`, `state/health.json`, and
  `state/notification_health.json` after the monitor step, including failed runs

Required repository secrets:

- `DATA_GO_KR_SERVICE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional repository secrets:

- `CHECK_DAYS` defaults to `3`
- `SEND_TELEGRAM` defaults to `true`
- `SEND_EMPTY_SUMMARY` defaults to `false` and is retained for configuration
  compatibility; the daily heartbeat now provides the empty-run status without
  sending a summary on all four runs

Set them in:

```text
Repository -> Settings -> Secrets and variables -> Actions -> New repository secret
```

Manual run path:

```text
Repository -> Actions -> Bid Monitor -> Run workflow
```

## Duplicate Notification State

Duplicate Telegram sends are prevented by `state/sent_bids.json`.

- The file stores each sent `bid_id` and its last delivered content hash.
- The file is intentionally committed so GitHub Actions can keep state between scheduled runs.
- A `bid_id` is added only after Telegram send succeeds.
- An unchanged hash is not sent again; a changed hash is sent as a changed notice.
- Legacy ID-only entries are baselined once without creating a duplicate alert.
- Runtime DB and logs are not used for cross-run duplicate prevention in GitHub Actions.

## Operational Reliability

- The required `getBidPblancListInfoServc` request is attempted up to three
  times for timeout, request errors, HTTP 429, and HTTP 5xx responses, with
  `2s` then `5s` backoff. Permanent HTTP 4xx responses are not retried.
- Optional enrichment endpoints keep their skip-on-failure behavior.
- After all core retries fail, the run stays failed and sends at most one
  masked failure alert per KST date.
- The first successfully completed run each KST date sends one heartbeat, even
  when there are no new A/B notices. Later runs still send only eligible new or
  changed A/B notices.
- When a persisted core incident recovers, one recovery status is sent. If that
  day's heartbeat is also due, both statuses are combined into one message.
- Check incidents in GitHub Actions and `state/health.json`. Daily alert gates
  and recovery state are stored atomically in `state/notification_health.json`.

## Ignored Local Files

Do not commit local secrets or runtime outputs:

- `.env`
- `.venv/`
- `data/bids.sqlite`
- `logs/`
- `downloads/`

Current `.gitignore` blocks these paths. Before committing, verify with:

```powershell
git status --short
git ls-files .env .venv data/bids.sqlite logs downloads
```

The second command should print nothing.

## Nara API

Normal runs use:

```text
https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc
```

Diagnostics:

```powershell
python main.py --test-nara
python main.py --diagnose-nara
```

## Git Remote

Expected remote:

```powershell
git remote set-url origin https://github.com/dgilink/bid_monitor.git
git remote -v
```
