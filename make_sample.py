#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成测试用体检数据 sample.xlsx（覆盖：正常/校验失败/平台退回/重复 各类场景）"""
import random
from datetime import date, timedelta

from openpyxl import Workbook

random.seed(42)
wb = Workbook()
ws = wb.active
ws.title = "体检结果"
ws.append(["人员编号", "项目", "结果", "单位", "体检日期"])

ITEMS = [("血压", "mmHg", lambda: f"{random.randint(90,140)}/{random.randint(60,90)}"),
         ("心率", "次/分", lambda: str(random.randint(60, 100))),
         ("血糖", "mmol/L", lambda: str(round(random.uniform(4, 7), 1))),
         ("身高", "cm", lambda: str(random.randint(150, 190))),
         ("体重", "kg", lambda: str(random.randint(45, 90))),
         ("视力", "", lambda: str(round(random.uniform(4.0, 5.3), 1))),
         ("血常规", "", lambda: "未见异常"),
         ("心电图", "", lambda: "窦性心律")]

base = date(2026, 9, 20)
rows = []
# 1) 正常数据 40 行
for i in range(40):
    pid = f"P{10001 + i % 20}"
    item, unit, gen = random.choice(ITEMS)
    d = base + timedelta(days=random.randint(0, 5))
    rows.append([pid, item, gen(), unit, d.strftime("%Y-%m-%d")])
# 2) 文件内重复 3 行（复制前面的行）
rows += [list(r) for r in rows[:3]]
# 3) 校验失败：空编号 / 空结果 / 非法日期 / 未来日期
rows.append(["", "血压", "120/80", "mmHg", "2026-09-21"])
rows.append(["P20001", "血糖", "", "mmol/L", "2026-09-21"])
rows.append(["P20002", "心率", "75", "次/分", "不是日期"])
rows.append(["P20003", "心率", "80", "次/分", "2027-01-01"])
# 4) 平台退回：人员不存在(含404) / 项目不在目录 / 结果含ERR
rows.append(["P40401", "血压", "118/76", "mmHg", "2026-09-22"])
rows.append(["P20004", "基因检测", "阴性", "", "2026-09-22"])
rows.append(["P20005", "血糖", "ERR_VALUE", "mmol/L", "2026-09-22"])

random.shuffle(rows)
for r in rows:
    ws.append(r)
wb.save("sample.xlsx")
print(f"sample.xlsx 生成完毕，共 {len(rows)} 行数据")
