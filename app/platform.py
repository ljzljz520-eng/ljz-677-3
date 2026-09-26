"""健康平台客户端（模拟实现）。

真实项目里这里应替换为 HTTP 调用；当前用确定性规则模拟平台返回，
便于演示各种平台异常：人员未建档 / 结果超范围 / 平台维护 / 限流 / 冲突 / 重复。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .catalog import ITEMS

PLATFORM_URL = "https://health.example.gov/api/v1/exam-results"

_ITEM_MAP = {it["code"]: it for it in ITEMS}

# 进程内计数器：模拟平台对人员的限流
_rate_buckets: dict[str, int] = {}


def reset_rate_limits() -> None:
    """新一轮批处理开始时重置限流窗口（模拟限流按时间窗口恢复）。"""
    _rate_buckets.clear()


@dataclass
class PlatformResponse:
    success: bool
    code: str
    message: str
    reference_no: str | None = None


def submit(person_id: str, item_code: str, result: float, unit: str, exam_date: str) -> PlatformResponse:
    time.sleep(0.004)  # 模拟网络往返

    # E5001 平台维护（特定人员号段作为演示开关）
    if person_id == "EMP0500":
        return PlatformResponse(False, "E5001", "健康平台维护中（预计 30 分钟后恢复），该记录请稍后重试")

    # E4001 人员未建档：EMP0001-EMP0600 及 VIP 开头为已建档
    registered = person_id.startswith("VIP") or (
        person_id.startswith("EMP") and person_id[3:].isdigit() and 1 <= int(person_id[3:]) <= 600
    )
    if not registered:
        return PlatformResponse(False, "E4001", f"人员 {person_id} 未在健康平台建立健康档案")

    # E4003 结果超出医学合理硬范围
    item = _ITEM_MAP.get(item_code)
    if item and result is not None and not (item["hard_low"] <= result <= item["hard_high"]):
        return PlatformResponse(
            False, "E4003",
            f"{item['name']}结果 {result} {unit} 超出医学合理范围 "
            f"[{item['hard_low']}, {item['hard_high']}]，疑似录入错误",
        )

    # E3002 平台侧数据冲突（确定性触发：EMP0009 的 ALT 与既往记录冲突）
    if person_id == "EMP0009" and item_code == "ALT":
        return PlatformResponse(False, "E3002", "平台已有的 ALT 记录与本次结果冲突，请核对后联系平台管理员")

    # E5003 限流：平台限制每人每分钟 1 条，同批次第 2 条被限流（窗口随每轮批处理重置）
    if person_id == "EMP0007":
        _rate_buckets[person_id] = _rate_buckets.get(person_id, 0) + 1
        if _rate_buckets[person_id] == 2:
            return PlatformResponse(False, "E5003", "平台接口限流（每分钟最多 1 条/人），请稍后重试")

    # E3001 / 成功由调用方结合平台落库结果判断
    return PlatformResponse(True, "S0000", "接收成功")
