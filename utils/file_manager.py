"""
trades.csv 관리 — atomic write + 일일 백업
trades.csv = Single Source of Truth
"""
import os
import shutil
from pathlib import Path
from datetime import datetime
from config.log_config import setup_logger

logger = setup_logger("file")

DATA_DIR = Path.home() / "phoenix_v3" / "data"
TRADES_PATH = DATA_DIR / "trades.csv"
TRADES_HEADER = "timestamp,stock_code,stock_name,action,price,qty,pnl,pnl_pct,reason,order_id,k_value,rsi,spread,slippage\n"

def ensure_trades_file():
    """trades.csv가 없으면 헤더만 생성"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_PATH.exists():
        TRADES_PATH.write_text(TRADES_HEADER, encoding="utf-8")

def append_trade(row_dict):
    """거래 기록 추가 (atomic write)"""
    ensure_trades_file()
    line = ",".join(str(row_dict.get(k, "")) for k in TRADES_HEADER.strip().split(","))

    # atomic write: 임시 파일에 쓴 후 rename
    tmp = TRADES_PATH.with_suffix(".tmp")
    existing = TRADES_PATH.read_text(encoding="utf-8")
    tmp.write_text(existing + line + "\n", encoding="utf-8")
    tmp.replace(TRADES_PATH)
    logger.info(f"거래 기록 추가: {row_dict.get('stock_name', '?')} {row_dict.get('action', '?')}")

def daily_backup():
    """일일 백업 (16:00 리포트 시)"""
    if not TRADES_PATH.exists():
        return
    today = datetime.now().strftime("%Y%m%d")
    backup = DATA_DIR / f"trades_backup_{today}.csv"
    shutil.copy2(TRADES_PATH, backup)
    logger.info(f"일일 백업 완료: {backup.name}")
