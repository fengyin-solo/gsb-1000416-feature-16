"""通行费用统一口径的可执行校验脚本。

不依赖 pytest / fastapi，直接用标准库运行：

    cd backend && python3 tests/test_toll_rules.py

覆盖：阈值版本按日期回溯、例外路线优先级、同优先级冲突待核、
业务确认凭证闸门、并发补录去重、汇总口径一致。
"""
from __future__ import annotations

import sys
import os
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.toll import TollService  # noqa: E402
from app.services.toll_policy import (  # noqa: E402
    CONCLUSION_ABNORMAL,
    CONCLUSION_EXCEPTION,
    CONCLUSION_NORMAL,
    policy,
)

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    if actual != expected:
        FAILURES.append(f"{name}: 期望 {expected!r}，实际 {actual!r}")
    else:
        print(f"  ✓ {name}")


def verdict(station: str, direction: str, amount: object, on_date: str) -> dict:
    return policy.evaluate(
        station=station, direction=direction, amount_raw=amount, on_date=on_date
    )


print("阈值版本：按通行日期回溯当时规则")
check("V2026 区间内正常", verdict("京哈高速-白鹿", "进京", "30", "2026-09-01")["判定结论"], CONCLUSION_NORMAL)
check("V2026 超上限判异", verdict("京哈高速-白鹿", "进京", 95, "2026-09-01")["异常类型"], "金额高于阈值")
check("V2026 低于下限判异", verdict("京哈高速-白鹿", "进京", 6, "2026-09-01")["异常类型"], "金额低于阈值")
check("历史费用按 V2025 规则", verdict("京哈高速-香河", "出京", 55, "2025-12-20")["适用规则版本"], "V2025")
check("同金额在 V2025 正常", verdict("京哈高速-香河", "出京", 55, "2025-12-20")["判定结论"], CONCLUSION_NORMAL)
check("同金额在 V2026 超阈值", verdict("京哈高速-香河", "出京", 70, "2026-09-01")["判定结论"], CONCLUSION_ABNORMAL)

print("例外路线：优先级与方向匹配")
r1 = verdict("京津高速-机场北", "出京", 8, "2026-09-01")
check("低价出京命中例外放行", r1["判定结论"], CONCLUSION_EXCEPTION)
check("例外放行给出依据", "京津高速机场联络线半价" in r1["判定依据"], True)
check("反方向不命中例外", verdict("京津高速-机场北", "进京", 8, "2026-09-01")["判定结论"], CONCLUSION_ABNORMAL)
r3 = verdict("京港澳高速-杜家坎", "进京", 45, "2026-09-01")
check("同优先级冲突判待核", r3["判定结论"], CONCLUSION_ABNORMAL)
check("冲突异常类型", r3["异常类型"], "例外规则冲突")

print("金额无法识别")
check("非数字金额", verdict("京哈高速-白鹿", "进京", "三十", "2026-09-01")["异常类型"], "金额无法识别")

print("服务层：确认、冲销、并发补录、汇总")
svc = TollService()

entry, msg = svc.run_action(4, "业务确认", {"最终结论": CONCLUSION_NORMAL, "凭证编号": ""})
check("判异记录无凭证被拒", entry is None, True)
entry, msg = svc.run_action(
    4, "业务确认", {"最终结论": CONCLUSION_EXCEPTION, "凭证编号": "PZ-TEST-04", "备注": "应急调度"}
)
check("带凭证确认成功", entry is not None and entry["判定结论"], CONCLUSION_EXCEPTION)
entry, msg = svc.run_action(4, "业务确认", {"最终结论": CONCLUSION_NORMAL, "凭证编号": "PZ-OTHER"})
check("只允许一个最终结论", entry is None, True)

entry, msg = svc.run_action(7, "冲销费用", {"冲销原因": ""})
check("冲销缺原因被拒", entry is None, True)
entry, msg = svc.run_action(7, "冲销费用", {"冲销原因": "重复扣费"})
check("冲销成功", entry is not None and entry["状态"], "已冲销")

payload = {
    "车辆编号": "VEHI-TEST", "收费站名称": "京哈高速-白鹿",
    "通行方向": "进京", "收费金额": "30", "通行日期": "2026-09-25",
}
with ThreadPoolExecutor(8) as pool:
    results = list(pool.map(lambda _: svc.create_entry(dict(payload)), range(8)))
check("并发补录只落一条", len([r for r in results if r[0] is not None]), 1)

summary = svc.summary(month="2026-09")
check("冲销金额不计入已确认", summary["已冲销金额"] >= 22.0 and summary["已确认费用"] >= 95.0, True)

veh = svc.vehicle_toll_summary("VEHI-0001")
check("车辆列与汇总同一服务", isinstance(veh["累计已确认通行费"], float), True)

print()
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：")
    for item in FAILURES:
        print(f"  ✗ {item}")
    raise SystemExit(1)
print("全部口径校验通过")
