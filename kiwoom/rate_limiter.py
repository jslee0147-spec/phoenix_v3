"""
API 호출 간격 제어 + 우선순위 큐
- 최소 200ms 간격 (초당 5건)
- 4단계 우선순위: CRITICAL > HIGH > NORMAL > LOW
"""
import time
import threading
from enum import IntEnum

class Priority(IntEnum):
    CRITICAL = 0  # 손절 매도, 계좌 하드캡
    HIGH = 1      # 매수, 체결확인
    NORMAL = 2    # 계좌평가, 체결정보
    LOW = 3       # 차트, 수급

class RateLimiter:
    def __init__(self, min_interval_ms=200):
        self._min_interval = min_interval_ms / 1000.0
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self):
        """다음 호출까지 필요한 만큼 대기"""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.time()
