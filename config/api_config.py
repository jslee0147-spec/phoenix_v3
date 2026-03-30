"""
키움증권 REST API 설정
- 도메인, 엔드포인트, 호출 제한
"""
import os
from dotenv import load_dotenv

load_dotenv()

# 모의/실전 전환
PHOENIX_MODE = os.getenv("PHOENIX_MODE", "mock")
BASE_URL = "https://api.kiwoom.com" if PHOENIX_MODE == "live" else "https://mockapi.kiwoom.com"

# 인증 정보
APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
SECRET_KEY = os.getenv("KIWOOM_SECRET_KEY", "")
ACCOUNT_NO = os.getenv("KIWOOM_ACCOUNT", "")
ACCOUNT_PW = os.getenv("KIWOOM_PASSWORD", "")

# 텔레그램
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 노션
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_TRADE_DB_ID = "f9227148119d44c0b4b5037bffbd74b2"
NOTION_DASHBOARD_PAGE_ID = "33316ce4-4537-8167-879f-c7bf67af6da7"

# URL 패턴 (같은 URL이라도 api-id 헤더가 다르면 다른 API)
URLS = {
    "token": "/oauth2/token",
    "revoke": "/oauth2/revoke",
    "acnt": "/api/dostk/acnt",   # 계좌/시세 조회 (api-id로 구분)
    "ordr": "/api/dostk/ordr",   # 주문 (api-id로 구분)
}

# API 호출 제한
MIN_INTERVAL_MS = 200  # 최소 200ms 간격 (초당 5건)
