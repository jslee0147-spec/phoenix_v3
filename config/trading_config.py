"""
PHOENIX 트레이딩 파라미터
설계서 v3.2 기준
"""

# ===== RADAR =====
# 0차 필터: 시장 상태
KOSPI_MA_PERIOD = 20             # 코스피 20일 이동평균
KOSPI_RECOVERY_PCT = 2.0         # 회복 시 +2% 이상 (관찰 모드에서 검증 예정)

# 1차 필터: 모멘텀
MOMENTUM_SHORT_DAYS = 20         # 단기 모멘텀 (20일 수익률)
MOMENTUM_LONG_DAYS = 60          # 중기 모멘텀 (60일 수익률)
MA_SHORT = 5                     # 단기 이평선
MA_LONG = 20                     # 장기 이평선
MIN_TRADE_VALUE = 10_0000_0000   # 일평균 거래대금 10억원
MIN_MARKET_CAP = 2000_0000_0000  # 시총 하한 2,000억
MAX_MARKET_CAP = 5_0000_0000_0000  # 시총 상한 5조

# 2차 필터: 수급
SUPPLY_CONSECUTIVE_DAYS = 3      # 외국인/기관 연속 순매수 3일
MAX_CREDIT_RATIO = 30            # 신용비율 상한 30%

# 3차 필터: 제외
REENTRY_BLOCK_DAYS = 5           # 종목 재진입 금지 (5거래일)

# RADAR 출력
MAX_WATCHLIST = 10               # 감시 종목 최대 10개

# ===== STRIKE =====
# K값 동적 조정 (ATR 기반)
K_HIGH_VOL = 0.4                 # ATR비율 > 1.5 (고변동성)
K_MID_VOL = 0.5                  # ATR비율 1.0~1.5
K_LOW_VOL = 0.6                  # ATR비율 < 1.0
ATR_PERIOD = 14                  # ATR 계산 기간

# 진입 조건
VOLUME_RATIO = 1.5               # 거래량 20일 평균 대비 배수
RSI_MIN = 50                     # RSI 하한
RSI_MAX = 70                     # RSI 상한
RSI_PERIOD = 14
GAP_DOWN_REJECT = -2.0           # 갭 하락 -2% 이상이면 거부
MIN_TRADE_AMOUNT = 5000_0000     # 최소 거래대금 5,000만원
MAX_SPREAD_PCT = 0.5             # 호가 스프레드 최대 0.5%

# 진입 시간
STRIKE_START = "09:10"
STRIKE_END = "14:45"

# ===== SHIELD =====
# 청산 조건
TP_PCT = 7.0                     # 목표 수익률 +7% (세전)
TP_AFTER_FEE = 6.7               # 세후 +6.7%
TRAILING_ACTIVATE_PCT = 5.0      # 트레일링 활성화 기준 +5%
TRAILING_STOP_PCT = 2.0          # 고점 대비 -2%
SL_PCT = -3.0                    # 손절 -3%
SL_TIGHT_PCT = -2.0              # 공매도 급증 시 강화 SL -2%
TIME_STOP_DAYS = 5               # 시간 손절 5거래일
CLOSE_SELL_TIME = "14:50"        # 장마감 손절 시간
CLOSE_SELL_HOLD_DAYS = 1         # 보유 1일 이상
CLOSE_SELL_LOSS_PCT = -2.0       # 수익 -2% 미만

# 폴링
POLL_INTERVAL_SEC = 30           # 30초 폴링

# 안전장치
MAX_POSITIONS = 3                # 최대 동시 보유 3종목
POSITION_SIZE_PCT = 25           # 종목당 최대 25%
MIN_CASH_PCT = 25                # 현금 보유 최소 25%
DAILY_LOSS_LIMIT = -50000        # 일일 최대 손실 -5만원 (계좌 하드캡)
WEEKLY_LOSS_LIMIT = -100000      # 주간 -10만원
MONTHLY_LOSS_LIMIT = -150000     # 월간 -15만원
MAX_DAILY_TRADES = 5             # 일일 최대 거래 5회

# ===== 슬리피지 추정 (관찰 모드) =====
SLIPPAGE_BUY = 0.3               # 매수 시 +0.3%
SLIPPAGE_SELL = 0.2              # 매도 시 -0.2%

# ===== 실제 손익비 =====
# 거래세 0.18% + 수수료 0.015% 반영
# 실제 TP +6.7% / SL -3.2% = 손익비 2.09:1
