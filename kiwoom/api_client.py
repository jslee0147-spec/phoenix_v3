"""
키움증권 REST API 클라이언트
- Bearer 토큰 인증
- api-id 헤더로 API 구분
- 레이트 리미터 적용
- 에러 핸들링 + 재시도
"""
import requests
from config.api_config import BASE_URL, URLS
from kiwoom.endpoints import ENDPOINTS
from kiwoom.rate_limiter import RateLimiter
from config.log_config import setup_logger

logger = setup_logger("api")

class KiwoomClient:
    def __init__(self):
        self._rate_limiter = RateLimiter(200)
        self._token_manager = None  # 순환참조 방지, 외부에서 설정
        self._session = requests.Session()

    def set_token_manager(self, tm):
        self._token_manager = tm

    def raw_post(self, path, body, api_id=None, auth=True):
        """저수준 HTTP POST — 토큰/레이트리미터 적용"""
        url = f"{BASE_URL}{path}"
        headers = {"Content-Type": "application/json;charset=UTF-8"}

        if api_id:
            headers["api-id"] = api_id

        if auth and self._token_manager:
            headers["authorization"] = f"Bearer {self._token_manager.token}"

        self._rate_limiter.wait()

        try:
            resp = self._session.post(url, json=body, headers=headers, timeout=10)

            # HTTP 상태코드 검사 (#7 성단 지적)
            if resp.status_code == 401:
                logger.error(f"API 인증 실패 [{api_id}]: 토큰 만료 또는 무효")
                return {"return_code": -401, "return_msg": "인증 실패 (401)"}
            if resp.status_code >= 500:
                logger.error(f"API 서버 에러 [{api_id}]: HTTP {resp.status_code}")
                return {"return_code": -500, "return_msg": f"서버 에러 ({resp.status_code})"}
            if resp.status_code != 200:
                logger.warning(f"API 비정상 응답 [{api_id}]: HTTP {resp.status_code}")

            data = resp.json()

            if data.get("return_code", -1) != 0:
                logger.warning(f"API 에러 [{api_id}]: {data.get('return_msg', 'unknown')}")

            return data
        except requests.exceptions.Timeout:
            logger.error(f"API 타임아웃 [{api_id}]: {url}")
            return {"return_code": -1, "return_msg": "timeout"}
        except Exception as e:
            logger.error(f"API 예외 [{api_id}]: {e}")
            return {"return_code": -1, "return_msg": str(e)}

    def call(self, api_id, body=None, retry=3):
        """
        API 호출 (api_id 기반).
        엔드포인트 자동 매핑 + 재시도.
        """
        if api_id not in ENDPOINTS:
            raise ValueError(f"알 수 없는 API ID: {api_id}")

        url_key, desc = ENDPOINTS[api_id]
        path = URLS[url_key]

        for attempt in range(retry):
            result = self.raw_post(path, body or {}, api_id=api_id)

            if result.get("return_code") == 0:
                return result

            # 시세과부하(-200) → 1초 대기 후 재시도
            if result.get("return_code") == -200:
                import time
                logger.warning(f"시세과부하 [{api_id}], {attempt+1}/{retry} 재시도...")
                time.sleep(1)
                continue

            # 기타 에러 → 재시도
            if attempt < retry - 1:
                import time
                time.sleep(0.5)
                continue

        return result

    # ===== 편의 메서드 =====

    def get_account_eval(self):
        """계좌평가현황 (kt00004) — SHIELD 폴링 핵심"""
        return self.call("kt00004", {"qry_tp": "0", "dmst_stex_tp": "KRX"})

    def get_balance(self):
        """체결잔고 (kt00005)"""
        return self.call("kt00005", {"dmst_stex_tp": "KRX"})

    def get_deposit(self):
        """예수금상세현황 (kt00001)"""
        return self.call("kt00001", {"prcgb": "00"})

    def buy_market(self, stock_code, qty):
        """시장가 매수 (kt10000)"""
        return self.call("kt10000", {
            "dmst_stex_tp": "KRX",
            "stk_cd": stock_code,
            "ord_qty": str(qty),
            "ord_uv": "",
            "trde_tp": "3",
            "cond_uv": ""
        })

    def sell_market(self, stock_code, qty):
        """시장가 매도 (kt10001)"""
        return self.call("kt10001", {
            "dmst_stex_tp": "KRX",
            "stk_cd": stock_code,
            "ord_qty": str(qty),
            "ord_uv": "",
            "trde_tp": "3",
            "cond_uv": ""
        })

    def get_daily_chart(self, stock_code, count=60):
        """주식일봉차트 (ka10081) — 모멘텀/ATR 계산"""
        return self.call("ka10081", {
            "stk_cd": stock_code,
            "base_dt": "",
            "updn_tp": "",
            "mod_prc_tp": "1"
        })

    def get_quote(self, stock_code):
        """체결정보 (ka10003) — 현재가"""
        return self.call("ka10003", {"stk_cd": stock_code})

    def get_orderbook(self, stock_code):
        """호가 (ka10004) — 스프레드 확인"""
        return self.call("ka10004", {"stk_cd": stock_code})

    def get_supply_demand(self, stock_code):
        """주식일주월시분 (ka10005) — 수급"""
        return self.call("ka10005", {
            "stk_cd": stock_code,
            "period_tp": "D"
        })

    def get_consecutive_trades(self, stock_code):
        """기관외국인연속매매현황 (ka10131)"""
        return self.call("ka10131", {"stk_cd": stock_code})

    def get_short_selling(self, stock_code):
        """공매도추이 (ka10014)"""
        return self.call("ka10014", {"stk_cd": stock_code})

    def get_program_trading(self, stock_code):
        """종목별프로그램매매현황 (ka90004)"""
        return self.call("ka90004", {"stk_cd": stock_code})

    def get_kospi_index(self):
        """업종현재가 (ka20001) — 코스피 지수"""
        return self.call("ka20001", {"mrkt_tp": "0", "inds_cd": "001"})
