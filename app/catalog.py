"""体检项目目录：项目代码、名称、单位、参考范围与物理可行区间。

- ref_min/ref_max：医学参考范围，超出只给 WARNING，不影响上送。
- hard_min/hard_max：物理上不可能出现的数值，超出本地判为校验失败。
"""

ITEMS = {
    "GLU":   {"name": "空腹血糖",   "unit": "mmol/L",  "ref_min": 3.9,  "ref_max": 6.1,  "hard_min": 0.5,  "hard_max": 60.0},
    "HBA1C": {"name": "糖化血红蛋白", "unit": "%",       "ref_min": 4.0,  "ref_max": 6.0,  "hard_min": 2.0,  "hard_max": 20.0},
    "TC":    {"name": "总胆固醇",   "unit": "mmol/L",  "ref_min": 2.8,  "ref_max": 5.2,  "hard_min": 0.5,  "hard_max": 30.0},
    "TG":    {"name": "甘油三酯",   "unit": "mmol/L",  "ref_min": 0.45, "ref_max": 1.7,  "hard_min": 0.05, "hard_max": 20.0},
    "HDL":   {"name": "高密度脂蛋白", "unit": "mmol/L",  "ref_min": 1.04, "ref_max": 1.55, "hard_min": 0.2,  "hard_max": 5.0},
    "LDL":   {"name": "低密度脂蛋白", "unit": "mmol/L",  "ref_min": 1.9,  "ref_max": 3.1,  "hard_min": 0.2,  "hard_max": 15.0},
    "SBP":   {"name": "收缩压",     "unit": "mmHg",    "ref_min": 90.0, "ref_max": 140.0, "hard_min": 50.0, "hard_max": 300.0},
    "DBP":   {"name": "舒张压",     "unit": "mmHg",    "ref_min": 60.0, "ref_max": 90.0,  "hard_min": 30.0, "hard_max": 200.0},
    "HR":    {"name": "心率",       "unit": "次/分",    "ref_min": 60.0, "ref_max": 100.0, "hard_min": 20.0, "hard_max": 250.0},
    "BMI":   {"name": "体重指数",   "unit": "kg/m2",   "ref_min": 18.5, "ref_max": 24.0, "hard_min": 10.0, "hard_max": 60.0},
    "ALT":   {"name": "谷丙转氨酶", "unit": "U/L",     "ref_min": 9.0,  "ref_max": 50.0, "hard_min": 0.0,  "hard_max": 5000.0},
    "AST":   {"name": "谷草转氨酶", "unit": "U/L",     "ref_min": 15.0, "ref_max": 40.0, "hard_min": 0.0,  "hard_max": 5000.0},
    "CREA":  {"name": "肌酐",       "unit": "umol/L",  "ref_min": 57.0, "ref_max": 111.0, "hard_min": 10.0, "hard_max": 2000.0},
    "BUN":   {"name": "尿素氮",     "unit": "mmol/L",  "ref_min": 3.1,  "ref_max": 8.0,  "hard_min": 0.5,  "hard_max": 80.0},
    "UA":    {"name": "尿酸",       "unit": "umol/L",  "ref_min": 208.0, "ref_max": 428.0, "hard_min": 50.0, "hard_max": 1500.0},
    "HGB":   {"name": "血红蛋白",   "unit": "g/L",     "ref_min": 115.0, "ref_max": 150.0, "hard_min": 30.0, "hard_max": 250.0},
    "WBC":   {"name": "白细胞计数", "unit": "10^9/L",  "ref_min": 3.5,  "ref_max": 9.5,  "hard_min": 0.2,  "hard_max": 100.0},
}

# 单位写法归一化（常见等价写法 -> 标准单位）
UNIT_ALIASES = {
    "mmol/l": "mmol/L",
    "umol/l": "umol/L",
    "umol/l ": "umol/L",
    "μmol/l": "umol/L",
    "ug/l": "ug/L",
    "u/l": "U/L",
    "kg/m²": "kg/m2",
    "kg/m^2": "kg/m2",
    "次/min": "次/分",
    "bpm": "次/分",
    "mmhg": "mmHg",
    "%": "%",
}


def normalize_unit(raw: str) -> str:
    key = raw.strip().replace("　", "")
    alias = UNIT_ALIASES.get(key.lower())
    return alias if alias else key


def find_item(raw: str):
    """按代码（不区分大小写）或中文名匹配项目。"""
    key = raw.strip()
    if not key:
        return None, None
    upper = key.upper()
    if upper in ITEMS:
        return upper, ITEMS[upper]
    for code, item in ITEMS.items():
        if item["name"] == key:
            return code, item
    return None, None


def catalog_rows():
    return [
        {"code": code, "name": v["name"], "unit": v["unit"],
         "ref_range": f"{v['ref_min']} ~ {v['ref_max']}"}
        for code, v in ITEMS.items()
    ]
