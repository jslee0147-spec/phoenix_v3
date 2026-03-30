#!/usr/bin/env python3
"""
🔥 PHOENIX v3.2 — 국내주식 자동매매 시스템
메인 진입점 + 장중 루프

설계: 별이+성단 | 승인: 소니+준수 | 구현: 미츠리

스케줄:
  06:00  RADAR-PREP (수급 반전 확인)
  08:50  시스템 시작 + 토큰 발급
  09:00  수급 반전 대상 즉시 매도
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
from config.trading_config import OBSERVATION_MODE
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
    mode_str = f"{PHOENIX_MODE}" + (" + 👀관찰모드" if OBSERVATION_MODE else "")
    logger.info(f"🔥 PHOENIX v3.2 시작 (모드: {mode_str})")

    client = KiwoomClient()
    tm = TokenManager(client)
    client.set_token_manager(tm)
    _ = tm.token

    acct = client.get_account_eval()
    deposit = client._parse_number(acct.get("entr", "0"))  # #23 일관성
    logger.info(f"✅ 계좌: {acct.get('acnt_nm')} | 예수금: {deposit:,.0f}원")

    obs_msg = "\n👀 <b>관찰 모드 ON</b> — 실주문 없음, 가상매매만 기록" if OBSERVATION_MODE else ""
    send_telegram(
        f"🔥 <b>PHOENIX v3.2 시작</b>\n"
        f"모드: {mode_str}\n"
        f"예수금: {deposit:,.0f}원{obs_msg}"
    )

    radar = Radar(client)
    strike = Strike(client)
    shield = Shield(client)
    shield.load_positions()

    return client, tm, radar, strike, shield


def run_radar_prep(radar, shield):
    """#16 RADAR-PREP (06:00) — 수급 반전 확인 + 마킹"""
    logger.info("🔥 RADAR-PREP 실행")
    sell_codes = radar.run_prep(shield.positions)
    shield.sell_targets.update(sell_codes)
    logger.info(f"  수급 반전 청산 대상: {len(sell_codes)}종목")
    return sell_codes


def run_radar_scan(radar, shield):
    """#16 RADAR-SCAN (18:00) — 다음 날 후보 선별"""
    logger.info("🔥 RADAR-SCAN 실행")
    # 보유 종목 업종 수집 (섹터 분산)
    held_sectors = set()
    for pos in shield.positions:
        held_sectors.add(pos.get("sector", ""))

    # 종목 풀은 현재 빈 리스트 (관찰 모드에서 조건검색으로 채울 예정)
    candidates = []
    watchlist = radar.run_scan(candidates, held_sectors)
    return watchlist


def run_market_loop(client, tm, radar, strike, shield):
    """장중 메인 루프 (09:10~15:20, 30초 폴링)"""
    logger.info("🔥 장중 루프 시작")

    watchlist = radar.load_watchlist()
    logger.info(f"📋 감시 종목: {len(watchlist)}개")

    if watchlist:
        strike.update_market_avg_atr(watchlist)

    poll_count = 0

    while True:
        now = datetime.now()
        now_str = now.strftime("%H:%M")

        if now_str > "15:20":
            logger.info("🔥 15:20 — SHIELD 종료")
            break

        # #15 하드캡 도달 시 루프 중단
        if shield.halted:
            logger.critical("🚨 하드캡 도달 — 당일 거래 중단")
            send_telegram("🚨 <b>PHOENIX 당일 거래 중단</b>\n계좌 하드캡 도달")
            break

        poll_count += 1
        logger.info(f"--- 폴링 #{poll_count} ({now_str}) ---")

        # === SHIELD: 청산 감시 ===
        # #21 계좌 데이터 1회 조회 후 STRIKE와 공유
        acct = client.get_account_eval()
        closed = shield.poll(cached_acct=acct)
        for c in closed:
            logger.info(f"  청산: {c['name']} {c['pnl_pct']:+.1f}% ({c['reason']})")

        # === STRIKE: 진입 감시 (09:10~14:45) ===
        if tc.STRIKE_START <= now_str <= tc.STRIKE_END:
            if shield.daily_trades < tc.MAX_DAILY_TRADES and not shield.halted:
                signals = strike.scan_watchlist(
                    watchlist, shield.positions, tc.MAX_POSITIONS
                )
                for sig in signals:
                    # #21 이미 조회한 acct에서 현금 확인
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

        # 상태 로깅
        status = shield.get_status_summary()
        logger.info(f"  📊 보유 {status['positions']}종목 | "
                     f"PnL {status['daily_pnl']:+,.0f}원 | "
                     f"거래 {status['daily_trades']}회"
                     f"{' | ⚠️공매도경고 ' + str(status['short_alerts']) + '종목' if status['short_alerts'] else ''}")

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
        f"{chr(10) + '🚨 하드캡 도달로 조기 중단' if status['halted'] else ''}"
    )
    send_telegram(report)
    daily_backup()


def main():
    try:
        client, tm, radar, strike, shield = init_system()

        now_str = datetime.now().strftime("%H:%M")

        # #16 RADAR-PREP (06:00 시간대)
        if "05:50" <= now_str <= "06:30":
            run_radar_prep(radar, shield)

        # 장중이면 루프 가동
        if "09:00" <= now_str <= "15:20":
            # 09:00 — 수급 반전 대상 즉시 매도 (RADAR-PREP 결과)
            if shield.sell_targets:
                logger.info(f"📊 수급 반전 대상 {len(shield.sell_targets)}종목 즉시 매도")

            run_market_loop(client, tm, radar, strike, shield)
            run_daily_report(shield)

        # #16 RADAR-SCAN (18:00 시간대)
        elif "17:50" <= now_str <= "18:30":
            run_radar_scan(radar, shield)

        else:
            logger.info(f"현재 {now_str} — 장중 아님")
            market_ok = radar.check_market_condition()
            logger.info(f"시장 상태: {'✅ 양호' if market_ok else '❌ 불량'}")

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
