"""
🔥 PHOENIX SHIELD 엔진 — 청산/리스크 관리
장중 09:10~15:20 상시 가동, 30초 폴링

설계서 v3.2 기준
핵심 제약: 키움 REST API에 서버사이드 스탑로스 없음
→ 30초 폴링 + 로컬 스탑로스로 대응

청산 조건 (하나라도 충족 시 매도):
1. TP +7% (세전)
2. 트레일링 스탑: 고점 -2% (+5% 이상 시 활성화)
3. SL -3% (절대 손절)
4. 시간 손절: 5거래일
5. 장마감 손절: 14:50 — 보유 1일+ & 수익 -2% 미만
6. 수급 반전: RADAR-PREP에서 마킹된 종목
7. 공매도 급증: SL → -2%로 강화
+ 계좌 하드캡: 전체 -5만원 도달 시 전 종목 즉시 청산 + 당일 중단
"""
import json
import time
from pathlib import Path
from datetime import datetime, timedelta, date

from config.log_config import setup_logger
from config import trading_config as tc
from utils.alert import send_telegram
from utils.file_manager import append_trade

logger = setup_logger("shield")

DATA_DIR = Path.home() / "phoenix_v3" / "data"
POSITIONS_PATH = DATA_DIR / "positions.json"
HIGH_PRICES_PATH = DATA_DIR / "high_prices.json"


class Shield:
    def __init__(self, client):
        self.client = client
        self.positions = []
        self.high_prices = {}        # {code: 고점} — 트레일링용
        self.sell_targets = set()    # RADAR-PREP 수급 반전 청산 대상
        self.short_alert = set()     # 공매도 급증 종목 (SL 강화)
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.poll_fail_count = 0
        self.halted = False          # #15 하드캡 도달 시 당일 거래 중단
        self._last_short_check = 0   # #17 공매도 체크 타이머
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════
    # 30초 폴링 (메인 루프)
    # ══════════════════════════════════════
    def poll(self, cached_acct=None):
        """
        30초 폴링 1회 실행.
        cached_acct: STRIKE에서 이미 조회한 계좌 데이터 재활용 (#21)
        반환: 청산된 종목 리스트
        """
        # #15 하드캡 도달 시 전체 중단
        if self.halted:
            return []

        closed_list = []

        try:
            # 1. 계좌평가현황 조회 (#21: 캐시된 데이터 우선 사용)
            acct = cached_acct or self.client.get_account_eval()

            if acct.get("return_code") != 0:
                self.poll_fail_count += 1
                logger.warning(f"⚠️ 폴링 실패 ({self.poll_fail_count}/3)")

                # 비상 프로토콜: 3회 연속 실패 (90초)
                if self.poll_fail_count >= 3:
                    logger.critical("🚨 폴링 3회 연속 실패! 비상 프로토콜 발동!")
                    self._emergency_close_all("폴링 3회 연속 실패")
                return closed_list

            self.poll_fail_count = 0

            # 2. 계좌 레벨 하드캡 체크
            total_pnl = self._parse_number(acct.get("tot_pl_tot", "0"))
            if total_pnl <= tc.DAILY_LOSS_LIMIT:
                logger.critical(f"🚨 계좌 하드캡 도달! 총손익 {total_pnl:,.0f}원 ≤ {tc.DAILY_LOSS_LIMIT:,.0f}원")
                self._emergency_close_all(f"계좌 하드캡 ({total_pnl:,.0f}원)")
                self.halted = True  # #15 당일 거래 중단
                return closed_list

            # 3. 공매도 급증 체크 (#17 — 10분 간격)
            now = time.time()
            if now - self._last_short_check > 600:
                self._check_short_selling_alert()
                self._last_short_check = now

            # 4. 종목별 청산 조건 체크
            holdings = acct.get("stk_acnt_evlt_prst", [])
            self._sync_positions(holdings)

            for holding in holdings:
                result = self._check_exit(holding)
                if result:
                    closed_list.append(result)

        except Exception as e:
            logger.error(f"SHIELD 폴링 에러: {e}")
            self.poll_fail_count += 1
            if self.poll_fail_count >= 3:
                self._emergency_close_all(f"폴링 에러: {str(e)[:50]}")
                self.halted = True  # #15

        return closed_list

    # ══════════════════════════════════════
    # #17 공매도 급증 체크 (10분 간격)
    # ══════════════════════════════════════
    def _check_short_selling_alert(self):
        """보유 종목의 공매도 3일 연속 증가 여부 확인"""
        for pos in self.positions:
            code = pos["code"]
            name = pos.get("name", code)
            try:
                short_data = self.client.get_short_selling(code)
                if self._is_short_increasing(short_data):
                    if code not in self.short_alert:
                        self.short_alert.add(code)
                        logger.warning(f"⚠️ {name}: 공매도 3일 연속 증가! SL → {tc.SL_TIGHT_PCT}%로 강화")
                        send_telegram(f"⚠️ <b>공매도 급증</b>\n{name}\nSL {tc.SL_PCT}% → {tc.SL_TIGHT_PCT}%로 강화")
                else:
                    self.short_alert.discard(code)
            except Exception as e:
                logger.warning(f"공매도 체크 실패 {name}: {e}")
            time.sleep(0.25)  # API 간격

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

    # ══════════════════════════════════════
    # 청산 조건 체크 (7가지)
    # ══════════════════════════════════════
    def _check_exit(self, holding):
        """개별 종목 청산 조건 체크"""
        code = holding.get("stk_cd", "").replace("A", "")
        name = holding.get("stk_nm", code)
        cur_price = self._parse_number(holding.get("cur_prc", "0"))
        avg_price = self._parse_number(holding.get("avg_prc", "0"))
        qty = int(self._parse_number(holding.get("rmnd_qty", "0")))
        pnl_pct = self._parse_number(holding.get("pl_rt", "0"))
        pnl_amt = self._parse_number(holding.get("pl_amt", "0"))

        if qty <= 0 or cur_price <= 0:
            return None

        # 고점 갱신 (트레일링용)
        prev_high = self.high_prices.get(code, cur_price)
        if cur_price > prev_high:
            self.high_prices[code] = cur_price
            prev_high = cur_price
            self._save_high_prices()  # #19 영속화

        pos = self._find_position(code)
        hold_days = self._calc_hold_days(pos) if pos else 0
        now_str = datetime.now().strftime("%H:%M")

        # === 조건 1: TP +7% ===
        if pnl_pct >= tc.TP_PCT:
            return self._execute_sell(code, name, qty, "TP", pnl_pct, pnl_amt,
                                       f"🎯 목표 수익 달성 +{pnl_pct:.1f}%")

        # === 조건 2: 트레일링 스탑 ===
        if pnl_pct >= tc.TRAILING_ACTIVATE_PCT:
            drawdown = (cur_price - prev_high) / prev_high * 100
            if drawdown <= -tc.TRAILING_STOP_PCT:
                return self._execute_sell(code, name, qty, "트레일링", pnl_pct, pnl_amt,
                                           f"📉 트레일링 (고점 {prev_high:,.0f} → {cur_price:,.0f}, {drawdown:.1f}%)")

        # === 조건 7: 공매도 급증 → SL 강화 ===
        sl_threshold = tc.SL_TIGHT_PCT if code in self.short_alert else tc.SL_PCT

        # === 조건 3: SL (절대 손절) ===
        if pnl_pct <= sl_threshold:
            reason = "SL(공매도강화)" if code in self.short_alert else "SL"
            return self._execute_sell(code, name, qty, reason, pnl_pct, pnl_amt,
                                       f"🔻 손절 {pnl_pct:.1f}% (기준 {sl_threshold}%)")

        # === 조건 4: 시간 손절 (5거래일) ===
        if hold_days > tc.TIME_STOP_DAYS:
            return self._execute_sell(code, name, qty, "시간손절", pnl_pct, pnl_amt,
                                       f"⏰ {hold_days}거래일 보유 (한도 {tc.TIME_STOP_DAYS}일)")

        # === 조건 5: 장마감 손절 ===
        if now_str >= tc.CLOSE_SELL_TIME:
            if hold_days >= tc.CLOSE_SELL_HOLD_DAYS and pnl_pct < tc.CLOSE_SELL_LOSS_PCT:
                return self._execute_sell(code, name, qty, "장마감손절", pnl_pct, pnl_amt,
                                           f"🌅 장마감 손절 (보유 {hold_days}일, {pnl_pct:.1f}%)")

        # === 조건 6: 수급 반전 ===
        if code in self.sell_targets:
            return self._execute_sell(code, name, qty, "수급반전", pnl_pct, pnl_amt,
                                       "📊 수급 반전 (RADAR-PREP 마킹)")

        return None

    # ══════════════════════════════════════
    # 매도 실행
    # ══════════════════════════════════════
    def _execute_sell(self, code, name, qty, reason, pnl_pct, pnl_amt, detail):
        """시장가 매도 + 기록 + 알림"""
        logger.warning(f"🔥 SHIELD 청산: {name} — {detail}")

        result = self.client.sell_market(code, qty)

        if result.get("return_code") == 0:
            order_id = result.get("ord_no", "")
            logger.info(f"  ✅ {name}: 매도 완료! 주문번호 {order_id}, PnL {pnl_pct:+.1f}%")

            emoji = "💰" if pnl_pct > 0 else "🔻"
            send_telegram(
                f"{emoji} <b>[PHOENIX] 청산</b>\n"
                f"{name} ({code})\n"
                f"PnL: {pnl_pct:+.1f}% ({pnl_amt:+,.0f}원)\n"
                f"사유: {reason}\n{detail}"
            )

            append_trade({
                "timestamp": datetime.now().isoformat(),
                "stock_code": code,
                "stock_name": name,
                "action": "sell",
                "price": "",
                "qty": qty,
                "pnl": pnl_amt,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "order_id": order_id,
            })

            self.daily_pnl += pnl_amt
            self.daily_trades += 1
            self.high_prices.pop(code, None)
            self.sell_targets.discard(code)
            self.short_alert.discard(code)
            self._remove_position(code)
            self._save_high_prices()  # #19

            return {
                "code": code, "name": name,
                "pnl_pct": pnl_pct, "pnl_amt": pnl_amt,
                "reason": reason, "order_id": order_id,
            }
        else:
            logger.error(f"  ❌ {name}: 매도 실패 — {result.get('return_msg')}")
            send_telegram(f"🚨 <b>SHIELD 매도 실패!</b>\n{name}\n{result.get('return_msg')}")
            return None

    # ══════════════════════════════════════
    # 비상 프로토콜 (#20 개선: 개별 기록)
    # ══════════════════════════════════════
    def _emergency_close_all(self, reason):
        """전 포지션 강제 청산 + 개별 거래 기록"""
        logger.critical(f"🚨 비상 청산 발동: {reason}")
        send_telegram(f"🚨 <b>[PHOENIX] 비상 청산!</b>\n사유: {reason}")

        for pos in list(self.positions):
            code = pos["code"]
            name = pos.get("name", code)
            qty = pos.get("qty", 0)
            if qty > 0:
                result = self.client.sell_market(code, qty)
                if result.get("return_code") == 0:
                    order_id = result.get("ord_no", "")
                    logger.info(f"  🚨 비상 청산 완료: {name} {qty}주")

                    # #20 개별 거래 기록 + 알림
                    append_trade({
                        "timestamp": datetime.now().isoformat(),
                        "stock_code": code,
                        "stock_name": name,
                        "action": "sell",
                        "price": "",
                        "qty": qty,
                        "pnl": 0,
                        "pnl_pct": 0,
                        "reason": "비상청산",
                        "order_id": order_id,
                    })
                    send_telegram(f"🚨 비상 청산: {name} {qty}주 (주문 {order_id})")
                else:
                    logger.error(f"  🚨 비상 청산 실패: {name} — {result.get('return_msg')}")

        self.positions = []
        self.high_prices = {}
        self._save_positions()
        self._save_high_prices()

    # ══════════════════════════════════════
    # #18 거래일 수 계산 (주말 제외, 정확)
    # ══════════════════════════════════════
    def _calc_hold_days(self, pos):
        """보유 거래일 수 — 주말 직접 제외"""
        try:
            entry = datetime.fromisoformat(pos.get("entry_time", "")).date()
            today = date.today()
            biz_days = sum(1 for d in range((today - entry).days)
                           if (entry + timedelta(days=d + 1)).weekday() < 5)
            return max(biz_days, 0)
        except (ValueError, TypeError):
            return 0

    # ══════════════════════════════════════
    # 포지션 관리
    # ══════════════════════════════════════
    def _sync_positions(self, holdings):
        """API 응답과 로컬 포지션 동기화"""
        api_codes = set()
        for h in holdings:
            code = h.get("stk_cd", "").replace("A", "")
            qty = int(self._parse_number(h.get("rmnd_qty", "0")))
            if qty > 0:
                api_codes.add(code)
                if not self._find_position(code):
                    # 새 포지션 발견 (수동 매수 등)
                    # #24 주석: API에서 실제 매수 시점을 가져올 수 없어 현재 시점으로 설정
                    self.positions.append({
                        "code": code,
                        "name": h.get("stk_nm", code),
                        "qty": qty,
                        "entry_price": self._parse_number(h.get("avg_prc", "0")),
                        "entry_time": datetime.now().isoformat(),
                    })

        self.positions = [p for p in self.positions if p["code"] in api_codes]

    def _find_position(self, code):
        for p in self.positions:
            if p["code"] == code:
                return p
        return None

    def _remove_position(self, code):
        self.positions = [p for p in self.positions if p["code"] != code]
        self._save_positions()

    def add_position(self, trade_result):
        """STRIKE에서 매수 후 포지션 추가"""
        self.positions.append({
            "code": trade_result["code"],
            "name": trade_result["name"],
            "qty": trade_result["qty"],
            "entry_price": trade_result["price"],
            "entry_time": datetime.now().isoformat(),
        })
        self.daily_trades += 1
        self._save_positions()

    # ══════════════════════════════════════
    # 영속화 (#19 고점 포함)
    # ══════════════════════════════════════
    def _save_positions(self):
        POSITIONS_PATH.write_text(
            json.dumps(self.positions, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_high_prices(self):
        """#19 고점 데이터 영속화"""
        HIGH_PRICES_PATH.write_text(
            json.dumps(self.high_prices, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_high_prices(self):
        """#19 고점 데이터 로드"""
        if HIGH_PRICES_PATH.exists():
            try:
                self.high_prices = json.loads(HIGH_PRICES_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.high_prices = {}

    def load_positions(self):
        if POSITIONS_PATH.exists():
            try:
                self.positions = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.positions = []
        self._load_high_prices()  # #19 고점도 함께 로드

    # ══════════════════════════════════════
    # 유틸리티
    # ══════════════════════════════════════
    def _parse_number(self, s):
        try:
            return float(str(s).strip().lstrip("0") or "0")
        except (ValueError, TypeError):
            return 0.0

    def get_status_summary(self):
        return {
            "positions": len(self.positions),
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "sell_targets": len(self.sell_targets),
            "short_alerts": len(self.short_alert),
            "halted": self.halted,
        }
