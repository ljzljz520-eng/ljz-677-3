"""模拟健康平台上送接口。

真实环境可将 HealthPlatformClient.upload 替换为 HTTP 调用，
保持入参（逐行记录）与返回值结构不变即可。
"""
import random
import threading
import time
from datetime import datetime

from . import config


class PlatformError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


class HealthPlatformClient:
    """带幂等、速率限制、随机瞬时故障的模拟客户端（线程安全）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._seen_keys: dict[str, str] = {}   # 幂等键 -> 平台记录号
        self._call_window: list[float] = []    # 近 1 秒调用时间戳
        self.rate_limit_per_sec = 25
        self.fail_rate = config.PLATFORM_FAIL_RATE
        self.latency_ms = config.PLATFORM_LATENCY_MS
        # 模拟已建档人员：P1001 ~ P1050
        self.known_persons = {f"P{1001 + i}" for i in range(50)}

    def _check_rate_limit(self) -> None:
        now = time.time()
        with self._lock:
            self._call_window = [t for t in self._call_window if now - t < 1.0]
            if len(self._call_window) >= self.rate_limit_per_sec:
                raise PlatformError(
                    "RATE_LIMITED", "平台限流：每秒上送条数超限，请稍后重试",
                    retryable=True, http_status=429,
                )
            self._call_window.append(now)

    def lookup(self, key: str) -> str | None:
        """幂等查询：该指纹是否已被平台受理，返回平台记录号。"""
        with self._lock:
            return self._seen_keys.get(key)

    def upload(self, record: dict) -> dict:
        """上送单行，成功返回 {'record_id'}，失败抛 PlatformError。"""
        time.sleep(max(self.latency_ms, 0) / 1000.0)
        self._check_rate_limit()

        key = record["fingerprint"]
        with self._lock:
            duplicate = self._seen_keys.get(key)
            if duplicate:
                raise PlatformError(
                    "DUPLICATE_SUBMISSION",
                    f"平台幂等拦截：该结果已上送，平台记录号 {duplicate}",
                    retryable=False, http_status=409,
                )

        # 确定性业务异常
        person_id = record["person_id"]
        if person_id not in self.known_persons:
            raise PlatformError(
                "PERSON_NOT_FOUND",
                f"平台建档库中查无此人：{person_id}",
                retryable=False, http_status=422,
            )

        # 随机瞬时故障（网络抖动 / 平台内部错误），可重试
        if self.fail_rate > 0 and random.random() < self.fail_rate:
            code = random.choice(["PLATFORM_TIMEOUT", "PLATFORM_500", "BAD_GATEWAY"])
            messages = {
                "PLATFORM_TIMEOUT": "平台响应超时（ETIMEDOUT）",
                "PLATFORM_500": "平台内部错误（HTTP 500）",
                "BAD_GATEWAY": "平台网关异常（HTTP 502）",
            }
            raise PlatformError(code, messages[code], retryable=True, http_status=500)

        record_id = "HC" + datetime.now().strftime("%Y%m%d%H%M%S") + f"{random.randint(0, 999999):06d}"
        with self._lock:
            # 双重检查，避免同窗口内重复写入
            if key in self._seen_keys:
                raise PlatformError(
                    "DUPLICATE_SUBMISSION",
                    f"平台幂等拦截：该结果已上送，平台记录号 {self._seen_keys[key]}",
                    retryable=False, http_status=409,
                )
            self._seen_keys[key] = record_id
        return {"record_id": record_id}

    def reset(self) -> None:
        """测试用：清空平台侧幂等记忆。"""
        with self._lock:
            self._seen_keys.clear()
            self._call_window.clear()


# 单例，模拟一个长期在线的平台连接
client = HealthPlatformClient()
