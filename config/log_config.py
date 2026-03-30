"""
로깅 설정
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

LOG_DIR = Path.home() / "phoenix_v3" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def setup_logger(name="phoenix"):
    """구조화된 로거 생성"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s | %(message)s")

    # 파일 핸들러 (일별)
    today = datetime.now().strftime("%Y%m%d")
    fh = RotatingFileHandler(
        LOG_DIR / f"phoenix_{today}.log",
        maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # 콘솔 핸들러
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger
