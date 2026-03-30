"""
텔레그램 알림
"""
import requests
from config.api_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from config.log_config import setup_logger

logger = setup_logger("alert")

def send_telegram(message):
    """텔레그램 메시지 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정 없음, 알림 스킵")
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")
