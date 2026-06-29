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

## GitHub Actions

Workflow file:

```text
.github/workflows/bid-monitor.yml
```

The workflow:

- runs manually with `workflow_dispatch`
- runs every 2 days at `00:00 UTC`, which is `09:00 KST`
- uses Python 3.11
- installs dependencies from `requirements.txt`
- runs `python main.py`
- commits only `state/sent_bids.json` when successful sends update the state

Required repository secrets:

- `DATA_GO_KR_SERVICE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional repository secrets:

- `CHECK_DAYS` defaults to `3`
- `SEND_TELEGRAM` defaults to `true`
- `SEND_EMPTY_SUMMARY` defaults to `false`

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

- The file stores sent `bid_id` values only.
- The file is intentionally committed so GitHub Actions can keep state between scheduled runs.
- A `bid_id` is added only after Telegram send succeeds.
- Runtime DB and logs are not used for cross-run duplicate prevention in GitHub Actions.

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
