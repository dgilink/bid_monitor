# Nara Bid Monitor

나라장터 공고 목록을 조회하고 개발 관련 공고를 선별해 Telegram으로 알림을 보내는 Python 스크립트입니다.

## Local Setup

```powershell
cd C:\Users\user\PERSONAL\dev\bid_monitor
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`.env`에 실제 값을 입력합니다. `.env`는 절대 Git에 올리지 마세요.

```env
DATA_GO_KR_SERVICE_KEY=your_data_go_kr_service_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
CHECK_DAYS=3
SEND_TELEGRAM=true
SEND_EMPTY_SUMMARY=false
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

## GitHub Push

```powershell
git init
git remote add origin https://github.com/dgilink/bid_monitor.git
git add .
git status
git commit -m "Add Nara bid monitor with Telegram alerts"
git branch -M main
git push -u origin main
```

Before committing, confirm these are not staged:

- `.env`
- `.venv/`
- `data/*.sqlite`
- `logs/`
- `downloads/`

## GitHub Actions

Workflow file: `.github/workflows/bid-monitor.yml`

The workflow:

- uses Python 3.11
- installs dependencies with `pip install -r requirements.txt`
- runs `python main.py`
- supports manual execution with `workflow_dispatch`
- runs every 2 days at UTC 00:00, which is Korea time 09:00
- commits `state/sent_bids.json` after successful sends so duplicate Telegram alerts are avoided across GitHub Actions runs

Register secrets in GitHub:

`Repository -> Settings -> Secrets and variables -> Actions -> New repository secret`

Required secrets:

- `DATA_GO_KR_SERVICE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `CHECK_DAYS`
- `SEND_TELEGRAM`
- `SEND_EMPTY_SUMMARY`

Recommended values:

```text
CHECK_DAYS=3
SEND_TELEGRAM=true
SEND_EMPTY_SUMMARY=false
```

To run manually:

`Repository -> Actions -> Bid Monitor -> Run workflow`

## State And Storage

- `state/sent_bids.json` is committed and contains only sent `bid_id` values.
- `data/bids.sqlite` is local runtime storage and is ignored.
- `logs/` and `downloads/` are ignored.
- API keys, Telegram token, and chat id must only be stored in local `.env` or GitHub Secrets.

## Nara API Diagnostics

Normal runs use:

```text
https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc
```

Fallback endpoint combinations are tested only by:

```powershell
python main.py --diagnose-nara
```
