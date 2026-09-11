"""单行数据的本地校验：必填、格式、项目、单位、量值合理性、日期。"""
import hashlib
import re
from datetime import date, datetime

from . import catalog

PERSON_ID_RE = re.compile(r"^[A-Za-z0-9-]{3,30}$")
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d")


def to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        # 去掉浮点导入产生的多余尾数
        return ("%.10f" % value).rstrip("0").rstrip(".")
    return str(value).strip()


def parse_exam_date(raw):
    """接受 2026-01-01 / 2026/1/1 / 20260101，返回 (标准日期串, 错误)。"""
    text = to_text(raw)
    if not text:
        return "", "体检日期不能为空"
    dt = None
    if isinstance(raw, (datetime, date)):
        dt = raw if not isinstance(raw, datetime) else raw.date()
    else:
        for fmt in DATE_FORMATS:
            try:
                dt = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
    if dt is None:
        return "", f"体检日期格式不正确：{text}（支持 YYYY-MM-DD、YYYY/MM/DD、YYYYMMDD）"
    today = date.today()
    if dt > today:
        return "", f"体检日期不能晚于今天：{dt.isoformat()}"
    if dt.year < 2000:
        return "", f"体检日期过早：{dt.isoformat()}（不应早于 2000-01-01）"
    return dt.isoformat(), None


def parse_result(raw):
    text = to_text(raw)
    if not text:
        return None, "", "结果不能为空"
    try:
        value = float(text)
    except ValueError:
        return None, text, f"结果必须为数值：{text}"
    if value != value or value in (float("inf"), float("-inf")):
        return None, text, "结果不是有效数值"
    return value, text, None


def validate_row(raw: dict) -> dict:
    """对一行原始数据执行本地校验，返回规范化后的行信息。"""
    errors: list[str] = []
    warnings: list[str] = []

    # 人员编号
    person_id = to_text(raw.get("person_id"))
    if not person_id:
        errors.append("人员编号不能为空")
    elif not PERSON_ID_RE.match(person_id):
        errors.append(f"人员编号格式不正确：{person_id}（3-30 位字母、数字或连字符）")

    # 体检项目
    item_raw = to_text(raw.get("item"))
    code, item = catalog.find_item(item_raw) if item_raw else (None, None)
    item_name = item["name"] if item else item_raw
    if not item_raw:
        errors.append("体检项目不能为空")
    elif item is None:
        errors.append(f"未知体检项目：{item_raw}（可使用项目代码或名称，模板内附项目目录）")

    # 结果
    value, result_text, result_err = parse_result(raw.get("result"))
    if result_err:
        errors.append(result_err)

    # 单位
    unit_raw = to_text(raw.get("unit"))
    unit = catalog.normalize_unit(unit_raw) if unit_raw else ""
    if not unit_raw:
        errors.append("单位不能为空")
    elif item is not None and unit != item["unit"]:
        errors.append(f"单位不匹配：项目「{item['name']}」要求 {item['unit']}，实际为 {unit_raw}")

    # 体检日期
    exam_date, date_err = parse_exam_date(raw.get("exam_date"))
    if date_err:
        errors.append(date_err)

    # 量值合理区间（仅在可解析时检查）
    if value is not None and item is not None:
        if not (item["hard_min"] <= value <= item["hard_max"]):
            errors.append(
                f"结果超出可行区间：{result_text} {item['unit']}"
                f"（允许 {item['hard_min']} ~ {item['hard_max']}）"
            )
        elif not (item["ref_min"] <= value <= item["ref_max"]):
            warnings.append(
                f"结果超出参考范围：{result_text} {item['unit']}"
                f"（参考 {item['ref_min']} ~ {item['ref_max']}，仍将上送）"
            )

    fingerprint = ""
    if not errors:
        fingerprint = make_fingerprint(person_id, code, exam_date)

    return {
        "person_id": person_id,
        "item_code": code or "",
        "item_name": item_name,
        "result": result_text,
        "unit": unit,
        "exam_date": exam_date,
        "errors": errors,
        "warnings": warnings,
        "fingerprint": fingerprint,
    }


def make_fingerprint(person_id: str, item_code: str, exam_date: str) -> str:
    """同一人、同一项目、同一天视为同一体检结果。"""
    raw = f"{person_id.strip().upper()}|{item_code.upper()}|{exam_date}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
