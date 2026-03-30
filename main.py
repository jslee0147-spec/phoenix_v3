#!/usr/bin/env python3
"""
🔥 PHOENIX v3.2 — 국내주식 자동매매 시스템
메인 진입점 + 장중 루프

설계: 별이+성단 | 승인: 소니+준수 | 구현: 미츠리

스케줄:
  06:00  RADAR-PREP (수급 반전 확인)
  08:50  시스템 시작 + 토큰 발급
  09:10~ STRIKE + SHIELD 가동 (30초 폴링)
  14:45  STRIKE 비활성화
  14:50  장마감 손절
  15:20  SHIELD 종료
  16:00  일일 리포트
  18:00  RADAR-SCAN (다음 날 후보)
"""
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from config.log_config import setup_logger
from config.api_config import PHOENIX_MODE
from config import trading_config as tc
from kiwoom.api_client import KiwoomClient
from kiwoom.token_manager import TokenManager
from engines.radar import Radar
from engines.strike import Strike
from engines.shield import Shield
from utils.alert import send_telegram
from utils.file_manager import daily_backup

logger = setup_logger("main")


def init_system():
    """시스템 초기화"""
    logger.info("=" * 50)
    logger.info(f"🔥 PHOENIX v3.2 시작 (모드: {PHOENIX_MODE})")

    client = KiwoomClient()
    tm = TokenManager(client)
    client.set_token_manager(tm)
    _ = tm.token

    acct = client.get_account_eval()
    deposit = int(acct.get("entr", "0").lstrip("0") or "0")
    logger.info(f"✅ 계좌: {acct.get('acnt_nm')} | 예수금: {deposit:,}원")

    send_telegram(
        f"🔥 <b>PHOENIX v3.2 시작</b>\n"
        f"모드: {PHOENIX_MODE}\n"
        f"예수금: {deposit:,}원"
    )

    radar = Radar(client)
    strike = Strike(client)
    shield = Shield(client)
    shield.load_positions()

    return client, tm, radar, strike, shield


def run_market_loop(client, tm, radar, strike, shield):
    """장중 메인 루프 (09:10~15:20, 30초 폴링)"""
    logger.info("🔥 장중 루프 시작")

    # 감시 종목 로드
    watchlist = radar.load_watchlist()
    logger.info(f"📋 감시 종목: {len(watchlist)}개")

    # ATR 갱신
    if watchlist:
        strike.update_market_avg_atr(watchlist)

    poll_count = 0
    last_stale_check = time.time()

    while True:
        now = datetime.now()
        now_str = now.strftime("%H:%M")

        # 15:20 이후 종료
        if now_str > "15:20":
            logger.info("🔥 15:20 — SHIELD 종료")
            break

        poll_count += 1
        logger.info(f"--- 폴링 #{poll_count} ({now_str}) ---")

        # === SHIELD: 청산 감시 (항상) ===
        closed = shield.poll()
        for c in closed:
            logger.info(f"  청산: {c['name']} {c['pnl_pct']:+.1f}% ({c['reason']})")

        # === STRIKE: 진입 감시 (09:10~14:45) ===
        if tc.STRIKE_START <= now_str <= tc.STRIKE_END:
            if shield.daily_trades < tc.MAX_DAILY_TRADES:
                signals = strike.scan_watchlist(
                    watchlist,
                    shield.positions,
                    tc.MAX_POSITIONS
                )
                for sig in signals:
                    # 현금 확인
                    acct = client.get_account_eval()
                    cash = shield._parse_number(acct.get("entr", "0"))
                    min_cash = cash * tc.MIN_CASH_PCT / 100

                    available = cash - min_cash
                    if available <= 0:
                        logger.warning("현금 부족, 진입 스킵")
                        break

                    trade = strike.execute_buy(sig, available, tc.POSITION_SIZE_PCT / 100)
                    if trade:
                        shield.add_position(trade)
                        send_telegram(
                            f"📈 <b>[PHOENIX] 진입</b>\n"
                            f"{trade['name']} ({trade['code']})\n"
                            f"{trade['qty']}주 @ ~{trade['price']:,.0f}원\n"
                            f"K={sig['k_value']}, RSI={sig['rsi']:.0f}"
                        )

        # === 14:50: 장마감 손절 (SHIELD에서 자동 처리) ===

        # === 5분 가격 미갱신 체크 ===
        if time.time() - last_stale_check > 300:
            if shield.poll_fail_count > 0:
                send_telegram("⚠️ <b>PHOENIX 가격 미갱신 경고</b>\n5분 이상 정상 폴링 실패")
            last_stale_check = time.time()

        # 상태 로깅
        status = shield.get_status_summary()
        logger.info(f"  📊 보유 {status['positions']}종목 | "
                     f"당일 PnL {status['daily_pnl']:+,.0f}원 | "
                     f"거래 {status['daily_trades']}회")

        # 30초 대기
        time.sleep(tc.POLL_INTERVAL_SEC)


def run_daily_report(shield):
    """16:00 일일 리포트"""
    logger.info("📊 일일 리포트 생성")
    status = shield.get_status_summary()

    report = (
        f"📊 <b>[PHOENIX] 일일 리포트</b>\n"
        f"📅 {datetime.now().strftime('%Y-%m-%d')}\n"
        f"💰 당일 손익: {status['daily_pnl']:+,.0f}원\n"
        f"📈 거래 횟수: {status['daily_trades']}회\n"
        f"📋 잔여 보유: {status['positions']}종목"
    )
    send_telegram(report)
    logger.info(report.replace("<b>", "").replace("</b>", ""))

    # trades.csv 백업
    daily_backup()


def main():
    try:
        client, tm, radar, strike, shield = init_system()

        now_str = datetime.now().strftime("%H:%M")

        # 장중이면 루프 가동
        if "09:00" <= now_str <= "15:20":
            run_market_loop(client, tm, radar, strike, shield)

            # 장 마감 후 리포트
            run_daily_report(shield)
        else:
            logger.info(f"현재 {now_str} — 장중 아님")

            # RADAR-SCAN 테스트 (18:00용)
            if now_str >= "15:30":
                market_ok = radar.check_market_condition()
                logger.info(f"시장 상태: {'✅ 양호' if market_ok else '❌ 불량'}")

        # 토큰 폐기
        tm.revoke()
        logger.info("🔥 PHOENIX 정상 종료")

    except KeyboardInterrupt:
        logger.info("🔥 사용자 중단 (Ctrl+C)")
        send_telegram("⚠️ PHOENIX 수동 중단")
    except Exception as e:
        logger.critical(f"🔥 PHOENIX 크래시: {e}\n{traceback.format_exc()}")
        send_telegram(f"🚨 <b>PHOENIX 크래시!</b>\n{str(e)[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
