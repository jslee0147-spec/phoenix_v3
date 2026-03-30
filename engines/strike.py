"""
🔥 PHOENIX STRIKE 엔진 — 진입 판단
장중 09:10~14:45 활성

설계서 v3.2 기준
진입 조건 8가지 ALL 충족 시 시장가 매수:
1. 당일 시가 > 전일 종가 (갭 상승)
2. 현재가 > 시가 + (전일 변동폭 × K) (변동성 돌파)
3. 거래량 > 20일 평균 × 1.5 (거래량 폭발)
4. RSI(14) 50~70 (상승 초중기)
5. 09:10 이후 (장 초반 노이즈 회피)
6. 갭 하락 -2% 이상이면 거부
7. 거래대금 ≥ 5,000만원
8. 호가 스프레드 ≤ 0.5%

K값: ATR(14) 기반 동적 조정
동시 진입: 돌파폭/ATR 비율이 큰 순
"""
import json
import time
from pathlib import Path
from datetime import datetime

from config.log_config import setup_logger
from config import trading_config as tc

logger = setup_logger("strike")

DATA_DIR = Path.home() / "phoenix_v3" / "data"


class Strike:
    def __init__(self, client):
        self.client = client
        self._atr_cache = {}  # {code: atr_value}
        self._market_avg_atr = None

    # ══════════════════════════════════════
    # ATR 계산
    # ══════════════════════════════════════
    def calc_atr(self, prices_high, prices_low, prices_close, period=14):
        """True Range → ATR 계산"""
        if len(prices_high) < period + 1:
            return 0

        true_ranges = []
        for i in range(min(period, len(prices_high) - 1)):
            high = prices_high[i]
            low = prices_low[i]
            prev_close = prices_close[i + 1]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        if not true_ranges:
            return 0
        return sum(true_ranges) / len(true_ranges)

    def get_dynamic_k(self, stock_atr):
        """ATR 기반 K값 동적 조정"""
        if not self._market_avg_atr or self._market_avg_atr <= 0:
            return tc.K_MID_VOL  # 시장 평균 없으면 기본값

        ratio = stock_atr / self._market_avg_atr
        if ratio > 1.5:
            return tc.K_HIGH_VOL  # 고변동성 → K=0.4
        elif ratio < 1.0:
            return tc.K_LOW_VOL   # 저변동성 → K=0.6
        else:
            return tc.K_MID_VOL   # 중간 → K=0.5

    def update_market_avg_atr(self, watchlist):
        """감시 종목들의 평균 ATR 갱신"""
        atrs = []
        for stock in watchlist:
            code = stock["code"]
            chart = self.client.get_daily_chart(code)
            highs, lows, closes = self._extract_hlc(chart)
            atr = self.calc_atr(highs, lows, closes)
            if atr > 0:
                self._atr_cache[code] = atr
                atrs.append(atr)

        if atrs:
            self._market_avg_atr = sum(atrs) / len(atrs)
            logger.info(f"📊 시장 평균 ATR 갱신: {self._market_avg_atr:.2f} ({len(atrs)}종목)")

    # ══════════════════════════════════════
    # RSI 계산
    # ══════════════════════════════════════
    def calc_rsi(self, prices, period=14):
        """RSI(14) 계산"""
        if len(prices) < period + 1:
            return 50  # 데이터 부족 시 중립

        gains = []
        losses = []
        for i in range(period):
            change = prices[i] - prices[i + 1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # ══════════════════════════════════════
    # 진입 조건 체크 (8가지)
    # ══════════════════════════════════════
    def check_entry(self, stock):
        """
        진입 조건 8가지 ALL 충족 여부 체크.
        반환: (통과 여부, 신호 상세 dict)
        """
        code = stock["code"]
        name = stock.get("name", code)

        # 시간 체크 (#5)
        now = datetime.now()
        now_str = now.strftime("%H:%M")
        if now_str < tc.STRIKE_START or now_str > tc.STRIKE_END:
            return False, None

        # 현재 체결정보 조회
        quote = self.client.get_quote(code)
        if quote.get("return_code") != 0:
            return False, None

        cur_price = self._parse_number(quote.get("cur_prc", "0"))
        open_price = self._parse_number(quote.get("strt_prc", quote.get("open_prc", "0")))
        prev_close = self._parse_number(quote.get("prev_cls_prc", quote.get("ystd_cls_prc", "0")))
        volume = self._parse_number(quote.get("acml_vol", quote.get("acc_trdvol", "0")))
        trade_amount = self._parse_number(quote.get("acml_tr_pbmn", quote.get("acc_trdval", "0")))

        if cur_price <= 0 or open_price <= 0 or prev_close <= 0:
            return False, None

        # 조건 1: 갭 상승 (시가 > 전일 종가)
        if open_price <= prev_close:
            return False, None

        # 조건 6: 갭 하락 -2% 이상이면 거부
        gap_pct = (open_price - prev_close) / prev_close * 100
        if gap_pct <= tc.GAP_DOWN_REJECT:
            return False, None

        # 조건 2: 변동성 돌파 (현재가 > 시가 + 전일변동폭 × K)
        chart = self.client.get_daily_chart(code)
        highs, lows, closes = self._extract_hlc(chart)

        if len(highs) < 2:
            return False, None

        prev_range = highs[1] - lows[1]  # 전일 변동폭
        atr = self._atr_cache.get(code, self.calc_atr(highs, lows, closes))
        k_value = self.get_dynamic_k(atr)
        breakout_price = open_price + (prev_range * k_value)

        if cur_price <= breakout_price:
            return False, None

        # 조건 3: 거래량 > 20일 평균 × 1.5
        if len(closes) >= 20:
            # 일봉에서 거래량 추출
            vol_list = self._extract_volumes(chart)
            if len(vol_list) >= 20:
                avg_vol_20 = sum(vol_list[1:21]) / 20  # 오늘 제외 과거 20일
                if avg_vol_20 > 0 and volume < avg_vol_20 * tc.VOLUME_RATIO:
                    return False, None

        # 조건 4: RSI(14) 50~70
        rsi = self.calc_rsi(closes)
        if rsi < tc.RSI_MIN or rsi > tc.RSI_MAX:
            return False, None

        # 조건 7: 거래대금 ≥ 5,000만원
        if trade_amount > 0 and trade_amount < tc.MIN_TRADE_AMOUNT:
            return False, None

        # 조건 8: 호가 스프레드 ≤ 0.5%
        orderbook = self.client.get_orderbook(code)
        spread_pct = self._calc_spread(orderbook, cur_price)
        if spread_pct > tc.MAX_SPREAD_PCT:
            logger.info(f"  ❌ {name}: 스프레드 {spread_pct:.2f}% > {tc.MAX_SPREAD_PCT}%")
            return False, None

        # 돌파 강도 (동시 진입 우선순위용)
        breakout_strength = (cur_price - breakout_price) / atr if atr > 0 else 0

        signal = {
            "code": code,
            "name": name,
            "cur_price": cur_price,
            "open_price": open_price,
            "prev_close": prev_close,
            "breakout_price": round(breakout_price, 0),
            "k_value": k_value,
            "atr": round(atr, 2),
            "rsi": round(rsi, 1),
            "gap_pct": round(gap_pct, 2),
            "spread_pct": round(spread_pct, 3),
            "breakout_strength": round(breakout_strength, 3),
            "volume": volume,
            "trade_amount": trade_amount,
        }

        logger.info(f"  🎯 {name}: 진입 신호! 현재가 {cur_price:,.0f} > 돌파가 {breakout_price:,.0f} "
                     f"(K={k_value}, RSI={rsi:.0f}, 갭+{gap_pct:.1f}%, 스프레드 {spread_pct:.2f}%)")

        return True, signal

    # ══════════════════════════════════════
    # 스캔 (메인 루프에서 호출)
    # ══════════════════════════════════════
    def scan_watchlist(self, watchlist, current_positions, max_positions):
        """
        감시 종목 전체 스캔 → 진입 신호 발생 종목 반환.
        동시 진입 시 돌파폭/ATR 비율이 큰 순 정렬.
        """
        available_slots = max_positions - len(current_positions)
        if available_slots <= 0:
            return []

        held_codes = {p["code"] for p in current_positions}
        signals = []

        for stock in watchlist:
            if stock["code"] in held_codes:
                continue

            passed, signal = self.check_entry(stock)
            if passed and signal:
                signals.append(signal)

            time.sleep(0.05)  # API 간격

        # 동시 진입 우선순위: 돌파 강도 큰 순
        signals.sort(key=lambda s: s["breakout_strength"], reverse=True)

        # 빈 슬롯만큼만
        selected = signals[:available_slots]

        if selected:
            logger.info(f"🎯 STRIKE: {len(selected)}종목 진입 대상 선정")
            for s in selected:
                logger.info(f"  → {s['name']} (돌파강도 {s['breakout_strength']:.3f})")

        return selected

    # ══════════════════════════════════════
    # 주문 실행
    # ══════════════════════════════════════
    def execute_buy(self, signal, available_cash, position_pct):
        """
        시장가 매수 실행.
        관찰 모드: 실주문 없이 가상 매수 기록 (슬리피지 가산)
        """
        from config.trading_config import OBSERVATION_MODE, SLIPPAGE_BUY

        code = signal["code"]
        name = signal["name"]
        price = signal["cur_price"]

        buy_amount = available_cash * position_pct
        qty = int(buy_amount / price)

        if qty <= 0:
            logger.warning(f"  ❌ {name}: 매수 수량 0 (현금 부족)")
            return None

        # 관찰 모드: 실주문 없이 가상 기록
        if OBSERVATION_MODE:
            sim_price = price * (1 + SLIPPAGE_BUY / 100)  # 슬리피지 +0.3%
            logger.info(f"  👀 [관찰] {name}: 가상 매수 {qty}주 @ {sim_price:,.0f}원 (실제가 {price:,.0f} + 슬리피지 {SLIPPAGE_BUY}%)")
            return {
                "code": code,
                "name": name,
                "qty": qty,
                "price": sim_price,
                "order_id": f"OBS-{datetime.now().strftime('%H%M%S')}",
                "signal": signal,
                "observation": True,
            }

        logger.info(f"  📈 {name}: 시장가 매수 {qty}주 (약 {qty * price:,.0f}원)")
        result = self.client.buy_market(code, qty)

        if result.get("return_code") == 0:
            order_id = result.get("ord_no", "")
            logger.info(f"  ✅ {name}: 매수 완료! 주문번호 {order_id}")
            return {
                "code": code,
                "name": name,
                "qty": qty,
                "price": price,
                "order_id": order_id,
                "signal": signal,
            }
        else:
            logger.error(f"  ❌ {name}: 매수 실패 — {result.get('return_msg')}")
            return None

    # ══════════════════════════════════════
    # 유틸리티
    # ══════════════════════════════════════
    def _parse_number(self, s):
        try:
            return float(str(s).strip().lstrip("0") or "0")
        except (ValueError, TypeError):
            return 0.0

    def _extract_hlc(self, chart_data):
        """일봉에서 고가/저가/종가 리스트 추출"""
        highs, lows, closes = [], [], []
        items = chart_data.get("stk_dt_pole", chart_data.get("output", []))
        for item in items:
            h = self._parse_number(item.get("high_prc", item.get("stck_hgpr", "0")))
            l = self._parse_number(item.get("low_prc", item.get("stck_lwpr", "0")))
            c = self._parse_number(item.get("cls_prc", item.get("stck_clpr", "0")))
            if h > 0 and l > 0 and c > 0:
                highs.append(h)
                lows.append(l)
                closes.append(c)
        return highs, lows, closes

    def _extract_volumes(self, chart_data):
        """일봉에서 거래량 리스트 추출"""
        volumes = []
        items = chart_data.get("stk_dt_pole", chart_data.get("output", []))
        for item in items:
            v = self._parse_number(item.get("acml_vol", item.get("trd_vol", "0")))
            volumes.append(v)
        return volumes

    def _calc_spread(self, orderbook, cur_price):
        """호가 스프레드(%) 계산. 조회 실패 시 999 반환 → 진입 차단 (#12 성단 지적)"""
        if not orderbook or orderbook.get("return_code") != 0:
            return 999

        ask = self._parse_number(orderbook.get("ask_prc1", orderbook.get("askp1", "0")))
        bid = self._parse_number(orderbook.get("bid_prc1", orderbook.get("bidp1", "0")))

        if bid <= 0 or ask <= 0:
            return 999

        return (ask - bid) / cur_price * 100
