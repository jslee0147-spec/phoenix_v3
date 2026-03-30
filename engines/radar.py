"""
🔥 PHOENIX RADAR 엔진 — 종목 선별
- RADAR-PREP (06:00): 수급 데이터 수집 + 수급 반전 마킹
- RADAR-SCAN (18:00): 다음 날 매매 후보 종목 선별

설계서 v3.2 기준
0차: 코스피 20일선 시장 필터
1차: 모멘텀 스크리닝 (20일/60일 수익률, 이평선)
2차: 수급 확인 (외국인/기관 연속 순매수)
3차: 제외 조건 (관리종목, 재진입 금지, 섹터 분산)
"""
import json
import time
from pathlib import Path
from datetime import datetime

from config.log_config import setup_logger
from config import trading_config as tc

logger = setup_logger("radar")

DATA_DIR = Path.home() / "phoenix_v3" / "data"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"
POSITIONS_PATH = DATA_DIR / "positions.json"


class Radar:
    def __init__(self, client):
        self.client = client
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════
    # 0차 필터: 시장 상태 (코스피 20일선)
    # ══════════════════════════════════════
    def check_market_condition(self):
        """코스피가 20일 이동평균선 위인지 확인"""
        logger.info("📡 0차 필터: 코스피 시장 상태 확인")

        result = self.client.get_kospi_index()
        if result.get("return_code") != 0:
            logger.error(f"코스피 지수 조회 실패: {result}")
            return False

        # 코스피 현재가 파싱
        cur_price = self._parse_number(result.get("cur_prc", "0"))
        if cur_price <= 0:
            logger.warning("코스피 현재가 파싱 실패, 보수적으로 비활성화")
            return False

        # 일봉 데이터로 20일 이동평균 계산
        chart = self.client.call("ka10081", {"stk_cd": "0001", "base_dt": "", "updn_tp": "", "mod_prc_tp": "1"})
        prices = self._extract_close_prices(chart)

        if len(prices) < tc.KOSPI_MA_PERIOD:
            logger.warning(f"코스피 일봉 데이터 부족: {len(prices)}개")
            return True  # 데이터 부족 시 보수적으로 활성화

        ma20 = sum(prices[:tc.KOSPI_MA_PERIOD]) / tc.KOSPI_MA_PERIOD
        above_ma = cur_price > ma20

        logger.info(f"  코스피: {cur_price:,.0f} | 20일선: {ma20:,.0f} | "
                     f"{'✅ 상회' if above_ma else '❌ 하회'}")

        return above_ma

    # ══════════════════════════════════════
    # 1차 필터: 모멘텀 스크리닝
    # ══════════════════════════════════════
    def screen_momentum(self, candidates):
        """
        후보 종목에서 모멘텀 조건 충족 종목 필터링.
        candidates: [{"code": "005930", "name": "삼성전자", ...}, ...]
        """
        logger.info(f"📡 1차 필터: 모멘텀 스크리닝 ({len(candidates)}종목)")
        passed = []

        for stock in candidates:
            code = stock["code"]
            name = stock.get("name", code)

            # 시총 필터 (#4 성단 지적)
            market_cap = stock.get("market_cap", 0)
            if market_cap > 0 and (market_cap < tc.MIN_MARKET_CAP or market_cap > tc.MAX_MARKET_CAP):
                continue

            # 거래대금 필터 (#4 성단 지적)
            trade_value = stock.get("trade_value", 0)
            if trade_value > 0 and trade_value < tc.MIN_TRADE_VALUE:
                continue

            chart = self.client.get_daily_chart(code)
            prices = self._extract_close_prices(chart)

            if len(prices) < tc.MOMENTUM_LONG_DAYS:
                continue

            # 20일/60일 수익률
            ret_20d = (prices[0] - prices[tc.MOMENTUM_SHORT_DAYS - 1]) / prices[tc.MOMENTUM_SHORT_DAYS - 1] * 100
            ret_60d = (prices[0] - prices[tc.MOMENTUM_LONG_DAYS - 1]) / prices[tc.MOMENTUM_LONG_DAYS - 1] * 100

            if ret_60d <= 0:
                continue

            # 이평선 확인
            ma5 = sum(prices[:tc.MA_SHORT]) / tc.MA_SHORT
            ma20 = sum(prices[:tc.MA_LONG]) / tc.MA_LONG

            if prices[0] < ma20:  # 현재가 < 20일선
                continue
            if ma5 <= ma20:  # 5일선 ≤ 20일선
                continue

            stock["ret_20d"] = round(ret_20d, 2)
            stock["ret_60d"] = round(ret_60d, 2)
            stock["ma5"] = round(ma5, 0)
            stock["ma20"] = round(ma20, 0)
            stock["prices"] = prices  # ATR 계산용
            passed.append(stock)

            logger.info(f"  ✅ {name}: 20일 +{ret_20d:.1f}%, 60일 +{ret_60d:.1f}%, MA5>{ma5:.0f} MA20>{ma20:.0f}")

            time.sleep(0.05)  # API 호출 간격

        # 20일 수익률 상위 정렬
        passed.sort(key=lambda x: x["ret_20d"], reverse=True)
        logger.info(f"  1차 통과: {len(passed)}종목")
        return passed

    # ══════════════════════════════════════
    # 2차 필터: 수급 확인
    # ══════════════════════════════════════
    def check_supply_demand(self, candidates):
        """외국인/기관 연속 순매수 + 프로그램 + 공매도 + 신용비율"""
        logger.info(f"📡 2차 필터: 수급 확인 ({len(candidates)}종목)")
        passed = []

        for stock in candidates:
            code = stock["code"]
            name = stock.get("name", code)

            # 기관/외국인 연속매매 현황
            consec = self.client.get_consecutive_trades(code)
            foreign_days = self._parse_consecutive_buy_days(consec, "foreign")
            inst_days = self._parse_consecutive_buy_days(consec, "institution")

            if foreign_days < tc.SUPPLY_CONSECUTIVE_DAYS:
                continue
            if inst_days < tc.SUPPLY_CONSECUTIVE_DAYS:
                continue

            # 프로그램 매매 비차익 순매수
            prog = self.client.get_program_trading(code)
            if not self._check_program_buy(prog):
                continue

            # 공매도 3일 연속 증가 아닐 것
            short = self.client.get_short_selling(code)
            if self._is_short_increasing(short):
                continue

            # 신용비율 체크 (#5 성단 지적)
            credit = self.client.call("ka10033", {"stk_cd": code})
            credit_ratio = self._parse_number(credit.get("crdt_rt", "0"))
            if credit_ratio > tc.MAX_CREDIT_RATIO:
                logger.info(f"  ❌ {name}: 신용비율 과열 {credit_ratio:.1f}%")
                continue

            stock["foreign_consec"] = foreign_days
            stock["inst_consec"] = inst_days
            passed.append(stock)

            logger.info(f"  ✅ {name}: 외국인 {foreign_days}일연속, 기관 {inst_days}일연속")

            time.sleep(0.05)

        logger.info(f"  2차 통과: {len(passed)}종목")
        return passed

    # ══════════════════════════════════════
    # 3차 필터: 제외 조건
    # ══════════════════════════════════════
    def apply_exclusions(self, candidates, held_sectors=None):
        """관리종목, 재진입 금지, 섹터 분산 등"""
        logger.info(f"📡 3차 필터: 제외 조건 ({len(candidates)}종목)")

        # 최근 진입 이력 로드
        recent_entries = self._load_recent_entries()
        held_sectors = held_sectors or set()

        passed = []
        for stock in candidates:
            code = stock["code"]
            name = stock.get("name", code)

            # 최근 5거래일 내 재진입 금지
            if code in recent_entries:
                logger.info(f"  ❌ {name}: 재진입 금지 (최근 진입 이력)")
                continue

            # 섹터 분산 (같은 업종 2개 금지)
            sector = stock.get("sector", "기타")
            if sector in held_sectors:
                logger.info(f"  ❌ {name}: 섹터 중복 ({sector})")
                continue

            passed.append(stock)

        logger.info(f"  3차 통과: {len(passed)}종목")
        return passed[:tc.MAX_WATCHLIST]

    # ══════════════════════════════════════
    # RADAR-SCAN (메인)
    # ══════════════════════════════════════
    def run_scan(self, candidates, held_sectors=None):
        """
        RADAR-SCAN 실행 — 전체 파이프라인.
        candidates: 코스피+코스닥 종목 풀
        반환: watchlist (최대 10종목)
        """
        logger.info("=" * 50)
        logger.info("🔥 RADAR-SCAN 시작")

        # 0차: 시장 상태
        if not self.check_market_condition():
            logger.warning("❌ 코스피 20일선 하회 — RADAR 비활성화, 신규 진입 금지")
            self._save_watchlist([])
            return []

        # 1차: 모멘텀
        after_momentum = self.screen_momentum(candidates)

        # 2차: 수급
        after_supply = self.check_supply_demand(after_momentum)

        # 3차: 제외
        watchlist = self.apply_exclusions(after_supply, held_sectors)

        # 저장
        self._save_watchlist(watchlist)

        logger.info(f"🔥 RADAR-SCAN 완료: {len(watchlist)}종목 선정")
        for i, s in enumerate(watchlist, 1):
            logger.info(f"  #{i} {s.get('name', s['code'])} | "
                         f"20일 +{s.get('ret_20d', 0):.1f}% | "
                         f"외국인 {s.get('foreign_consec', 0)}일")

        return watchlist

    # ══════════════════════════════════════
    # RADAR-PREP (수급 반전 마킹)
    # ══════════════════════════════════════
    def run_prep(self, held_positions):
        """
        RADAR-PREP (06:00) — 보유 종목 수급 반전 확인.
        반환: 청산 대상 종목 코드 리스트
        """
        logger.info("=" * 50)
        logger.info("🔥 RADAR-PREP 시작 — 수급 반전 확인")

        sell_targets = []
        for pos in held_positions:
            code = pos["code"]
            name = pos.get("name", code)

            consec = self.client.get_consecutive_trades(code)
            foreign_days = self._parse_consecutive_buy_days(consec, "foreign")
            inst_days = self._parse_consecutive_buy_days(consec, "institution")

            # 수급 반전: 외국인+기관 순매수 끊김
            if foreign_days < tc.SUPPLY_CONSECUTIVE_DAYS or inst_days < tc.SUPPLY_CONSECUTIVE_DAYS:
                logger.warning(f"  ⚠️ {name}: 수급 반전! 외국인 {foreign_days}일, 기관 {inst_days}일 → 청산 대상")
                sell_targets.append(code)
            else:
                logger.info(f"  ✅ {name}: 수급 유지 (외국인 {foreign_days}일, 기관 {inst_days}일)")

            time.sleep(0.05)

        logger.info(f"🔥 RADAR-PREP 완료: 청산 대상 {len(sell_targets)}종목")
        return sell_targets

    # ══════════════════════════════════════
    # 유틸리티
    # ══════════════════════════════════════
    def _parse_number(self, s):
        """문자열 숫자 파싱 (앞의 0 패딩, 음수 포함)"""
        try:
            return float(str(s).strip().lstrip("0") or "0")
        except (ValueError, TypeError):
            return 0.0

    def _extract_close_prices(self, chart_data):
        """일봉 데이터에서 종가 리스트 추출 (최신→과거 순)"""
        prices = []
        # 키움 API 응답 구조에 따라 파싱
        items = chart_data.get("stk_dt_pole", [])
        if not items:
            items = chart_data.get("output", [])
        for item in items:
            close = self._parse_number(item.get("cls_prc", item.get("stck_clpr", "0")))
            if close > 0:
                prices.append(close)
        return prices

    def _parse_consecutive_buy_days(self, data, investor_type):
        """연속 순매수 일수 파싱"""
        # API 응답 구조에 따라 조정 필요
        try:
            if investor_type == "foreign":
                return int(self._parse_number(data.get("frgn_cont_buy_cnt", "0")))
            else:
                return int(self._parse_number(data.get("orgn_cont_buy_cnt", "0")))
        except (ValueError, TypeError):
            return 0

    def _check_program_buy(self, data):
        """프로그램 매매 비차익 순매수 확인"""
        try:
            non_arb = self._parse_number(data.get("non_arb_net_buy", "0"))
            return non_arb > 0
        except Exception:
            return True  # 파싱 실패 시 통과

    def _is_short_increasing(self, data):
        """공매도 3일 연속 증가 여부"""
        try:
            items = data.get("output", data.get("stk_shrt_sell", []))
            if len(items) < 3:
                return False
            volumes = [self._parse_number(i.get("shrt_qty", "0")) for i in items[:3]]
            return volumes[0] > volumes[1] > volumes[2]
        except Exception:
            return False

    def _load_recent_entries(self):
        """최근 5거래일 내 진입했던 종목 코드 (#6 성단 지적: 날짜 필터 추가)"""
        trades_path = DATA_DIR / "trades.csv"
        if not trades_path.exists():
            return set()
        try:
            import csv
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(days=7)  # 5거래일 ≈ 7일
            codes = set()
            with open(trades_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("action") == "buy":
                        try:
                            ts = datetime.fromisoformat(row.get("timestamp", ""))
                            if ts >= cutoff:
                                codes.add(row.get("stock_code", ""))
                        except (ValueError, TypeError):
                            pass
            return codes
        except Exception:
            return set()

    def _save_watchlist(self, watchlist):
        """watchlist.json 저장"""
        clean = []
        for s in watchlist:
            clean.append({
                "code": s["code"],
                "name": s.get("name", ""),
                "sector": s.get("sector", ""),
                "ret_20d": s.get("ret_20d", 0),
                "ret_60d": s.get("ret_60d", 0),
                "foreign_consec": s.get("foreign_consec", 0),
                "inst_consec": s.get("inst_consec", 0),
            })
        WATCHLIST_PATH.write_text(
            json.dumps({"updated": datetime.now().isoformat(), "watchlist": clean},
                       ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def load_watchlist(self):
        """저장된 watchlist 로드"""
        if not WATCHLIST_PATH.exists():
            return []
        try:
            data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
            return data.get("watchlist", [])
        except Exception:
            return []
