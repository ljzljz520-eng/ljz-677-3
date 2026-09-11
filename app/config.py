"""运行配置（均可通过环境变量覆盖）。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("HC_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "healthcheck.db"

# 单文件大小上限（MB）
MAX_CONTENT_LENGTH = int(os.environ.get("HC_MAX_UPLOAD_MB", "20")) * 1024 * 1024

# 模拟健康平台参数
PLATFORM_LATENCY_MS = float(os.environ.get("HC_PLATFORM_LATENCY_MS", "80"))
PLATFORM_FAIL_RATE = float(os.environ.get("HC_PLATFORM_FAIL_RATE", "0.05"))

ALLOWED_EXTENSIONS = {".xlsx"}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
