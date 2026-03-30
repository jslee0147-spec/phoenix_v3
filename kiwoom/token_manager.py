"""
토큰 발급/갱신/폐기 관리
- expires_dt 파싱으로 만료 시점 관리
- 만료 5분 전 자동 갱신
"""
import time
from datetime import datetime
from config.log_config import setup_logger

logger = setup_logger("token")

class TokenManager:
    def __init__(self, api_client):
        self._client = api_client
        self._token = None
        self._expires_at = 0  # Unix timestamp

    @property
    def token(self):
        """현재 유효한 토큰 반환. 만료 5분 전이면 자동 갱신."""
        if self._token and time.time() < self._expires_at - 300:
            return self._token
        # 갱신 필요
        logger.info("🔑 토큰 갱신 시작...")
        self.refresh()
        return self._token

    def refresh(self):
        """토큰 발급/갱신"""
        from config.api_config import APP_KEY, SECRET_KEY
        res = self._client.raw_post("/oauth2/token", {
            "grant_type": "client_credentials",
            "appkey": APP_KEY,
            "secretkey": SECRET_KEY
        }, api_id="au10001", auth=False)

        if res.get("return_code") == 0:
            self._token = res["token"]
            # expires_dt 파싱: "20260331134547" → datetime
            exp_str = res.get("expires_dt", "")
            if exp_str:
                exp_dt = datetime.strptime(exp_str, "%Y%m%d%H%M%S")
                self._expires_at = exp_dt.timestamp()
                logger.info(f"🔑 토큰 발급 완료, 만료: {exp_str}")
            else:
                # 만료 시점 불명 → 23시간 후로 가정
                self._expires_at = time.time() + 23 * 3600
                logger.warning("🔑 토큰 만료 시점 불명, 23시간으로 설정")
        else:
            logger.error(f"🔑 토큰 발급 실패: {res}")
            raise RuntimeError(f"토큰 발급 실패: {res.get('return_msg')}")

    def revoke(self):
        """토큰 폐기 (장 마감 후)"""
        if not self._token:
            return
        from config.api_config import APP_KEY, SECRET_KEY
        res = self._client.raw_post("/oauth2/revoke", {
            "appkey": APP_KEY,
            "secretkey": SECRET_KEY,
            "token": self._token
        }, api_id="au10002", auth=False)
        logger.info(f"🔑 토큰 폐기: {res.get('return_msg', 'unknown')}")
        self._token = None
        self._expires_at = 0
