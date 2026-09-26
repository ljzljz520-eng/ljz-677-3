"""体检项目目录：标准代码、别名、标准单位与参考区间。

参考区间只用于在界面标注结果是否偏高/偏低，不影响上送；
平台另有自己的硬校验区间（E4003）。
"""
from __future__ import annotations

ITEMS = [
    {"code": "GLU",  "name": "空腹血糖", "aliases": ["血糖", "葡萄糖", "GLU"],
     "units": ["mmol/L"], "low": 3.9, "high": 6.1, "hard_low": 0.5, "hard_high": 50.0},
    {"code": "ALT",  "name": "丙氨酸氨基转移酶", "aliases": ["谷丙转氨酶", "ALT", "GPT"],
     "units": ["U/L"], "low": 9, "high": 50, "hard_low": 0, "hard_high": 2000},
    {"code": "SBP",  "name": "收缩压", "aliases": ["高压", "SBP"],
     "units": ["mmHg"], "low": 90, "high": 140, "hard_low": 50, "hard_high": 260},
    {"code": "DBP",  "name": "舒张压", "aliases": ["低压", "DBP"],
     "units": ["mmHg"], "low": 60, "high": 90, "hard_low": 30, "hard_high": 180},
    {"code": "BMI",  "name": "体质指数", "aliases": ["BMI", "体重指数"],
     "units": ["kg/m2", "kg/m²"], "low": 18.5, "high": 24.0, "hard_low": 10, "hard_high": 60},
    {"code": "HGB",  "name": "血红蛋白", "aliases": ["HGB", "血色素"],
     "units": ["g/L"], "low": 115, "high": 150, "hard_low": 30, "hard_high": 250},
    {"code": "TC",   "name": "总胆固醇", "aliases": ["胆固醇", "TC"],
     "units": ["mmol/L"], "low": 2.8, "high": 5.2, "hard_low": 1.0, "hard_high": 20},
    {"code": "CREA", "name": "肌酐", "aliases": ["CREA", "Cr"],
     "units": ["umol/L", "μmol/L"], "low": 41, "high": 81, "hard_low": 10, "hard_high": 2000},
]

# 标准化单位展示（把 μmol/L、kg/m² 统一成 ASCII 友好形式存储）
CANONICAL_UNIT = {
    "kg/m²": "kg/m2",
    "μmol/l": "umol/L",
    "umol/l": "umol/L",
}


def canonical_unit(u: str) -> str:
    u = u.strip()
    return CANONICAL_UNIT.get(u.lower(), u)


def resolve_item(text: str) -> dict | None:
    """按代码或中文名/别名匹配项目，忽略大小写与空格。"""
    if not text:
        return None
    key = text.strip().lower().replace(" ", "")
    for it in ITEMS:
        names = [it["code"], it["name"], *it["aliases"]]
        if key in {n.lower().replace(" ", "") for n in names}:
            return it
    return None
