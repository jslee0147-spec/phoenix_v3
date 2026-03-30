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
+ 계좌 하드캡: 전체 -5만원 도달 시 전 종목 즉시 청산
"""
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

from config.log_config import setup_logger
from config import trading_config as tc
from utils.alert import send_telegram
from utils.file_manager import append_trade

logger = setup_logger("shield")

DATA_DIR = Path.home() / "phoenix_v3" / "data"
POSITIONS_PATH = DATA_DIR / "positions.json"


class Shield:
    def __init__(self, client):
        self.client = client
        self.positions = []          # 보유 종목 리스트
        self.high_prices = {}        # {code: 고점} — 트레일링용
        self.sell_targets = set()    # RADAR-PREP 수급 반전 청산 대상
        self.short_alert = set()     # 공매도 급증 종목 (SL 강화)
        self.daily_pnl = 0.0         # 당일 실현 손익
        self.daily_trades = 0        # 당일 거래 횟수
        self.poll_fail_count = 0     # 연속 폴링 실패 횟수
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ══════════════════════════════════════
    # 30초 폴링 (메인 루프)
    # ══════════════════════════════════════
    def poll(self):
        """
        30초 폴링 1회 실행.
        반환: 청산된 종목 리스트
        """
        closed_list = []

        try:
            # 1. 계좌평가현황 조회 (kt00004)
            acct = self.client.get_account_eval()

            if acct.get("return_code") != 0:
                self.poll_fail_count += 1
                logger.warning(f"⚠️ 폴링 실패 ({self.poll_fail_count}/3)")

                # 비상 프로토콜: 3회 연속 실패 (90초)
                if self.poll_fail_count >= 3:
                    logger.critical("🚨 폴링 3회 연속 실패! 비상 프로토콜 발동!")
                    self._emergency_close_all("폴링 3회 연속 실패")
                return closed_list

            self.poll_fail_count = 0  # 성공 시 리셋

            # 2. 계좌 레벨 하드캡 체크
            total_pnl = self._parse_number(acct.get("tot_pl_tot", "0"))
            if total_pnl <= tc.DAILY_LOSS_LIMIT:
                logger.critical(f"🚨 계좌 하드캡 도달! 총손익 {total_pnl:,.0f}원 ≤ {tc.DAILY_LOSS_LIMIT:,.0f}원")
                self._emergency_close_all(f"계좌 하드캡 ({total_pnl:,.0f}원)")
                return closed_list

            # 3. 종목별 청산 조건 체크
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

        return closed_list

    # ══════════════════════════════════════
    # 청산 조건 체크 (7가지)
    # ══════════════════════════════════════
    def _check_exit(self, holding):
        """개별 종목 청산 조건 체크. 반환: 청산 결과 dict or None"""
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

        # 포지션 정보
        pos = self._find_position(code)
        hold_days = self._calc_hold_days(pos) if pos else 0
        now = datetime.now()
        now_str = now.strftime("%H:%M")

        # === 조건 1: TP +7% ===
        if pnl_pct >= tc.TP_PCT:
            return self._execute_sell(code, name, qty, "TP", pnl_pct, pnl_amt,
                                       f"🎯 목표 수익 달성 +{pnl_pct:.1f}%")

        # === 조건 2: 트레일링 스탑 (고점 -2%, +5% 이상 시 활성화) ===
        if pnl_pct >= tc.TRAILING_ACTIVATE_PCT:
            drawdown = (cur_price - prev_high) / prev_high * 100
            if drawdown <= -tc.TRAILING_STOP_PCT:
                return self._execute_sell(code, name, qty, "트레일링", pnl_pct, pnl_amt,
                                           f"📉 트레일링 스탑 (고점 {prev_high:,.0f} → 현재 {cur_price:,.0f}, {drawdown:.1f}%)")

        # === 조건 7: 공매도 급증 → SL 강화 -2% ===
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

        # === 조건 5: 장마감 손절 (14:50, 보유 1일+ & 수익 -2% 미만) ===
        if now_str >= tc.CLOSE_SELL_TIME:
            if hold_days >= tc.CLOSE_SELL_HOLD_DAYS and pnl_pct < tc.CLOSE_SELL_LOSS_PCT:
                return self._execute_sell(code, name, qty, "장마감손절", pnl_pct, pnl_amt,
                                           f"🌅 장마감 손절 (보유 {hold_days}일, {pnl_pct:.1f}%)")

        # === 조건 6: 수급 반전 (RADAR-PREP 마킹) ===
        if code in self.sell_targets:
            return self._execute_sell(code, name, qty, "수급반전", pnl_pct, pnl_amt,
                                       f"📊 수급 반전 (RADAR-PREP 마킹)")

        return None

    # ══════════════════════════════════════
    # 매도 실행
    # ══════════════════════════════════════
    def _execute_sell(self, code, name, qty, reason, pnl_pct, pnl_amt, detail):
        """시장가 매도 실행 + 기록"""
        logger.warning(f"🔥 SHIELD 청산: {name} — {detail}")

        # 일일 거래 횟수 체크
        if self.daily_trades >= tc.MAX_DAILY_TRADES:
            logger.warning(f"  ⚠️ 일일 거래 한도 도달 ({tc.MAX_DAILY_TRADES}회), 매도만 허용")

        result = self.client.sell_market(code, qty)

        if result.get("return_code") == 0:
            order_id = result.get("ord_no", "")
            logger.info(f"  ✅ {name}: 매도 완료! 주문번호 {order_id}, PnL {pnl_pct:+.1f}%")

            # 텔레그램 알림
            emoji = "💰" if pnl_pct > 0 else "🔻"
            send_telegram(
                f"{emoji} <b>[PHOENIX] 청산</b>\n"
                f"{name} ({code})\n"
                f"PnL: {pnl_pct:+.1f}% ({pnl_amt:+,.0f}원)\n"
                f"사유: {reason}\n"
                f"{detail}"
            )

            # 거래 기록
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

            # 상태 업데이트
            self.daily_pnl += pnl_amt
            self.daily_trades += 1
            self.high_prices.pop(code, None)
            self.sell_targets.discard(code)
            self.short_alert.discard(code)
            self._remove_position(code)

            return {
                "code": code,
                "name": name,
                "pnl_pct": pnl_pct,
                "pnl_amt": pnl_amt,
                "reason": reason,
                "order_id": order_id,
            }
        else:
            logger.error(f"  ❌ {name}: 매도 실패 — {result.get('return_msg')}")
            send_telegram(f"🚨 <b>SHIELD 매도 실패!</b>\n{name}\n{result.get('return_msg')}")
            return None

    # ══════════════════════════════════════
    # 비상 프로토콜
    # ══════════════════════════════════════
    def _emergency_close_all(self, reason):
        """전 포지션 강제 청산"""
        logger.critical(f"🚨 비상 청산 발동: {reason}")
        send_telegram(f"🚨 <b>[PHOENIX] 비상 청산!</b>\n사유: {reason}")

        for pos in list(self.positions):
            code = pos["code"]
            name = pos.get("name", code)
            qty = pos.get("qty", 0)
            if qty > 0:
                result = self.client.sell_market(code, qty)
                if result.get("return_code") == 0:
                    logger.info(f"  🚨 비상 청산 완료: {name} {qty}주")
                else:
                    logger.error(f"  🚨 비상 청산 실패: {name} — {result.get('return_msg')}")

        self.positions = []
        self.high_prices = {}

    # ══════════════════════════════════════
    # 가격 미갱신 체크 (5분)
    # ══════════════════════════════════════
    def check_stale_prices(self):
        """마지막 성공 폴링으로부터 5분 이상 경과 시 긴급 알림"""
        # main.py 루프에서 호출
        pass  # 타이머는 main.py에서 관리

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
                    # 새로 발견된 포지션 (수동 매수 등)
                    self.positions.append({
                        "code": code,
                        "name": h.get("stk_nm", code),
                        "qty": qty,
                        "entry_price": self._parse_number(h.get("avg_prc", "0")),
                        "entry_time": datetime.now().isoformat(),
                    })

        # API에 없는 포지션 제거 (이미 청산됨)
        self.positions = [p for p in self.positions if p["code"] in api_codes]

    def _find_position(self, code):
        for p in self.positions:
            if p["code"] == code:
                return p
        return None

    def _remove_position(self, code):
        self.positions = [p for p in self.positions if p["code"] != code]

    def _calc_hold_days(self, pos):
        """보유 거래일 수 계산"""
        try:
            entry = datetime.fromisoformat(pos.get("entry_time", ""))
            delta = datetime.now() - entry
            # 주말 제외 근사: 총 일수 × 5/7
            biz_days = int(delta.days * 5 / 7)
            return max(biz_days, 0)
        except (ValueError, TypeError):
            return 0

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

    def _save_positions(self):
        """positions.json 저장"""
        POSITIONS_PATH.write_text(
            json.dumps(self.positions, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def load_positions(self):
        """positions.json 로드"""
        if POSITIONS_PATH.exists():
            try:
                self.positions = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.positions = []

    # ══════════════════════════════════════
    # 유틸리티
    # ══════════════════════════════════════
    def _parse_number(self, s):
        try:
            return float(str(s).strip().lstrip("0") or "0")
        except (ValueError, TypeError):
            return 0.0

    def get_status_summary(self):
        """현재 상태 요약 (로깅/리포트용)"""
        return {
            "positions": len(self.positions),
            "daily_pnl": self.daily_pnl,
            "daily_trades": self.daily_trades,
            "sell_targets": len(self.sell_targets),
            "short_alerts": len(self.short_alert),
        }
