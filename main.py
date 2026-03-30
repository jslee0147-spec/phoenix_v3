#!/usr/bin/env python3
"""
🔥 PHOENIX v3.2 — 국내주식 자동매매 시스템
메인 진입점

설계: 별이+성단 | 승인: 소니+준수 | 구현: 미츠리
"""
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config.log_config import setup_logger
from config.api_config import PHOENIX_MODE
from kiwoom.api_client import KiwoomClient
from kiwoom.token_manager import TokenManager
from engines.radar import Radar
from utils.alert import send_telegram

logger = setup_logger("main")


def init_system():
    """시스템 초기화 — 토큰 발급 + 클라이언트 생성"""
    logger.info("=" * 50)
    logger.info(f"🔥 PHOENIX v3.2 시작 (모드: {PHOENIX_MODE})")

    client = KiwoomClient()
    tm = TokenManager(client)
    client.set_token_manager(tm)

    # 토큰 발급
    token = tm.token
    logger.info(f"✅ 토큰 발급 완료")

    # 계좌 확인
    acct = client.get_account_eval()
    deposit = int(acct.get("entr", "0").lstrip("0") or "0")
    logger.info(f"✅ 계좌: {acct.get('acnt_nm')} | 예수금: {deposit:,}원")

    send_telegram(
        f"🔥 <b>PHOENIX v3.2 시작</b>\n"
        f"모드: {PHOENIX_MODE}\n"
        f"예수금: {deposit:,}원"
    )

    return client, tm


def run_radar_scan(client):
    """RADAR-SCAN 실행 (테스트용 — 실제로는 종목 풀 필요)"""
    radar = Radar(client)

    # 시장 상태 확인만 먼저
    market_ok = radar.check_market_condition()

    if market_ok:
        logger.info("✅ 시장 상태 양호 — RADAR 활성화 가능")
    else:
        logger.warning("❌ 시장 상태 불량 — 신규 진입 금지")

    return market_ok


def main():
    try:
        client, tm = init_system()

        # RADAR 시장 상태 체크
        run_radar_scan(client)

        # 토큰 폐기
        tm.revoke()
        logger.info("🔥 PHOENIX 정상 종료")

    except Exception as e:
        logger.critical(f"🔥 PHOENIX 크래시: {e}\n{traceback.format_exc()}")
        send_telegram(f"🚨 <b>PHOENIX 크래시!</b>\n{str(e)[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
