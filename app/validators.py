"""导入校验：格式/范围校验 + 文件内重复 + 跨任务历史重复识别。"""
from __future__ import annotations

from datetime import date

from .catalog import resolve_item
from .database import get_conn
from .parser import parse_date, parse_result_number

# 错误码 -> 文案
ERROR_LABELS = {
    "E1001": "人员编号为空",
    "E1002": "体检项目无法识别（不在项目目录）",
    "E1003": "检查结果为空",
    "E1004": "检查结果不是有效数值",
    "E1005": "单位与项目标准单位不符",
    "E1006": "体检日期格式不正确或为未来日期",
    "E2001": "文件内重复（人员+项目+体检日期）",
    "E2002": "历史任务已成功上送，疑似重复体检",
}

PLATFORM_LABELS = {
    "E3001": "平台已存在相同记录（重复上送）",
    "E3002": "平台数据冲突（同一检查存在结果冲突）",
    "E4001": "人员未在健康平台建档",
    "E4003": "结果超出医学合理范围",
    "E5001": "健康平台维护中，稍后重试",
    "E5002": "健康平台网络异常",
    "E5003": "触发平台限流，稍后重试",
    "S0000": "上送成功",
}


def _norm_person(p: str) -> str:
    return (p or "").strip().upper()


def validate_records(records: list[dict], task_id: int) -> dict:
    """把校验结果写入 rows 表，返回统计信息。"""
    seen: dict[tuple[str, str, str], int] = {}
    today = date.today().isoformat()

    insert_rows = []
    valid = invalid = dup = 0

    for rec in records:
        codes: list[str] = []
        person = _norm_person(rec["person_id"])
        result_raw = rec["result"].strip()
        unit = rec["unit"].strip()
        iso_date = parse_date(rec["exam_date"])

        if not person:
            codes.append("E1001")

        item = resolve_item(rec["item_raw"])
        item_code = item["code"] if item else None
        item_name = item["name"] if item else rec["item_raw"].strip()
        if not item:
            codes.append("E1002")

        if not result_raw:
            codes.append("E1003")
            result_num = None
        else:
            result_num = parse_result_number(result_raw)
            if result_num is None:
                codes.append("E1004")

        if item and unit not in item["units"]:
            codes.append("E1005")

        if not iso_date or iso_date > today:
            codes.append("E1006")

        is_dup = 0
        # 文件内重复（忽略本身已有格式错误时仍判断，帮助工作人员一次看全问题）
        key = (person, item_code or "", iso_date or "")
        if person and item_code and iso_date:
            if key in seen:
                codes.append("E2001")
                is_dup = 1
            else:
                seen[key] = rec["row_no"]
                # 跨任务历史重复
                with get_conn() as conn:
                    hit = conn.execute(
                        "SELECT 1 FROM platform_records WHERE person_id=? AND item_code=? AND exam_date=?",
                        (person, item_code, iso_date),
                    ).fetchone()
                if hit:
                    codes.append("E2002")
                    is_dup = 1

        if codes:
            status = "duplicate" if all(c in ("E2001", "E2002") for c in codes) else "invalid"
            invalid += 1 if status == "invalid" else 0
            dup += 1 if status == "duplicate" else 0
        else:
            status = "pending"
            valid += 1
        if is_dup and status == "invalid":
            dup += 1  # 同时有格式错误与重复，重复计数也要统计

        insert_rows.append(
            {
                "task_id": task_id,
                "row_no": rec["row_no"],
                "person_id": person,
                "item_code": item_code,
                "item_name": item_name,
                "result": result_raw,
                "result_num": result_num,
                "unit": unit,
                "exam_date": iso_date or rec["exam_date"].strip(),
                "status": status,
                "is_duplicate": is_dup,
                "error_codes": ",".join(codes),
                "error_messages": "；".join(ERROR_LABELS.get(c, c) for c in codes),
            }
        )

    with get_conn() as conn:
        conn.executemany(
            """INSERT INTO rows (task_id,row_no,person_id,item_code,item_name,result,result_num,
                                 unit,exam_date,status,is_duplicate,error_codes,error_messages)
               VALUES (:task_id,:row_no,:person_id,:item_code,:item_name,:result,:result_num,
                       :unit,:exam_date,:status,:is_duplicate,:error_codes,:error_messages)""",
            insert_rows,
        )
        conn.execute(
            """UPDATE tasks SET status='validated', total_rows=?, valid_rows=?, invalid_rows=?,
                                duplicate_rows=?, validated_at=datetime('now','localtime')
               WHERE id=?""",
            (len(insert_rows), valid, invalid, dup, task_id),
        )
        conn.commit()

    return {"total": len(insert_rows), "valid": valid, "invalid": invalid, "duplicate": dup}
