"""通行费用统一判定口径。

金额阈值、例外路线、判定依据都集中在本模块，车辆列表、过路明细、费用汇总
只能读取这里算出的同一份结论，不允许各自再写一套比较逻辑。

规则按版本冻结：历史过路记录按通行日期命中当时生效的版本，规则升级后
旧版本仍保留在这里，旧记录的结论不重算（见 services/toll.py 的快照字段）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 全局兜底阈值：收费站没有单列规则时使用；实付金额超出区间即待核
DEFAULT_AMOUNT_BAND = (0.0, 120.0)


@dataclass(frozen=True)
class ThresholdRule:
    """金额阈值口径：同一收费站同一方向允许的实付金额区间。"""

    station: str
    direction: str
    standard: float  # 应收标准金额（元）
    lower: float
    upper: float
    note: str = ""

    def hit(self, station: str, direction: str) -> bool:
        return self.station == station and self.direction == direction

    def describe(self) -> str:
        note = f"（{self.note}）" if self.note else ""
        return f"阈值规则·{self.station}·{self.direction} 标准{self.standard:g}元，区间[{self.lower:g},{self.upper:g}]元{note}"


@dataclass(frozen=True)
class ExceptionRoute:
    """例外路线口径：命中时按豁免/折扣/固定价覆盖阈值结论。

    priority 数字越小优先级越高；同一收费站+方向命中多条同级例外时
    无法自动取舍，标记待核并要求业务确认凭证裁决。
    """

    code: str
    station: str
    direction: str
    name: str
    priority: int
    mode: str  # exempt 全免 / fixed 固定价 / discount 按标准金额打折
    amount: float = 0.0
    note: str = ""

    def hit(self, station: str, direction: str) -> bool:
        if self.station != station:
            return False
        return self.direction == "双向" or self.direction == direction

    def payable(self, standard: float) -> float:
        if self.mode == "exempt":
            return 0.0
        if self.mode == "fixed":
            return round(self.amount, 2)
        return round(standard * self.amount, 2)

    def describe(self) -> str:
        if self.mode == "exempt":
            detail = "全免"
        elif self.mode == "fixed":
            detail = f"固定{self.amount:g}元"
        else:
            detail = f"标准金额×{self.amount:g}"
        return f"例外路线·{self.code}（{self.name}，优先级{self.priority}，{detail}）{('：' + self.note) if self.note else ''}"


@dataclass(frozen=True)
class PolicyVersion:
    """一版冻结的收费口径：生效区间 + 阈值表 + 例外路线表。"""

    version: str
    effective_from: str
    effective_to: str | None  # None 表示持续生效
    default_band: tuple[float, float]
    thresholds: tuple[ThresholdRule, ...]
    exceptions: tuple[ExceptionRoute, ...]
    changelog: str = ""

    def in_effect(self, day: str) -> bool:
        return day >= self.effective_from and (self.effective_to is None or day <= self.effective_to)

    def threshold_for(self, station: str, direction: str) -> ThresholdRule | None:
        for rule in self.thresholds:
            if rule.hit(station, direction):
                return rule
        return None

    def exceptions_for(self, station: str, direction: str) -> list[ExceptionRoute]:
        return [item for item in self.exceptions if item.hit(station, direction)]

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "default_band": list(self.default_band),
            "changelog": self.changelog,
            "thresholds": [
                {
                    "station": item.station,
                    "direction": item.direction,
                    "standard": item.standard,
                    "lower": item.lower,
                    "upper": item.upper,
                    "note": item.note,
                }
                for item in self.thresholds
            ],
            "exceptions": [
                {
                    "code": item.code,
                    "station": item.station,
                    "direction": item.direction,
                    "name": item.name,
                    "priority": item.priority,
                    "mode": item.mode,
                    "amount": item.amount,
                    "note": item.note,
                }
                for item in self.exceptions
            ],
        }


# 历史版本保留，不允许删除或改写；发布新口径只能追加新版本。
POLICY_VERSIONS: tuple[PolicyVersion, ...] = (
    PolicyVersion(
        version="TOLL-POLICY-2025.08",
        effective_from="2025-08-01",
        effective_to="2026-08-31",
        default_band=(0.0, 100.0),
        changelog="初版口径：四个主线收费站单列阈值，机场高速匝道节假日分流全免。",
        thresholds=(
            ThresholdRule("沪杭高速新桥站", "入城", 60.0, 55.0, 65.0, "一类客车"),
            ThresholdRule("沪杭高速新桥站", "出城", 60.0, 55.0, 65.0, "一类客车"),
            ThresholdRule("机场高速匝道站", "入城", 30.0, 25.0, 35.0, "一类客车"),
            ThresholdRule("机场高速匝道站", "出城", 30.0, 25.0, 35.0, "一类客车"),
            ThresholdRule("沈海高速太仓站", "入城", 85.0, 80.0, 90.0, "一类客车"),
            ThresholdRule("沈海高速太仓站", "出城", 85.0, 80.0, 90.0, "一类客车"),
            ThresholdRule("绕城高速东段站", "入城", 45.0, 40.0, 50.0, "一类客车"),
            ThresholdRule("绕城高速东段站", "出城", 45.0, 40.0, 50.0, "一类客车"),
        ),
        exceptions=(
            ExceptionRoute("EX-2025-AIR", "机场高速匝道站", "双向", "节假日机场分流专线", 1, "exempt", note="重大节假日免费分流"),
        ),
    ),
    PolicyVersion(
        version="TOLL-POLICY-2026.09",
        effective_from="2026-09-01",
        effective_to=None,
        default_band=DEFAULT_AMOUNT_BAND,
        changelog="2026年9月调价：主线站标准上调，新增云谷站；机场保障车队固定价、绿色冷链返程折扣、会展摆渡专线同级并行。",
        thresholds=(
            ThresholdRule("沪杭高速新桥站", "入城", 65.0, 60.0, 70.0, "一类客车"),
            ThresholdRule("沪杭高速新桥站", "出城", 65.0, 60.0, 70.0, "一类客车"),
            ThresholdRule("机场高速匝道站", "入城", 32.0, 28.0, 36.0, "一类客车"),
            ThresholdRule("机场高速匝道站", "出城", 32.0, 28.0, 36.0, "一类客车"),
            ThresholdRule("沈海高速太仓站", "入城", 90.0, 85.0, 95.0, "一类客车"),
            ThresholdRule("沈海高速太仓站", "出城", 90.0, 85.0, 95.0, "一类客车"),
            ThresholdRule("绕城高速东段站", "入城", 48.0, 43.0, 53.0, "一类客车"),
            ThresholdRule("绕城高速东段站", "出城", 48.0, 43.0, 53.0, "一类客车"),
            ThresholdRule("云谷高速新区站", "入城", 55.0, 50.0, 60.0, "一类客车"),
            ThresholdRule("云谷高速新区站", "出城", 55.0, 50.0, 60.0, "一类客车"),
        ),
        exceptions=(
            ExceptionRoute("EX-AIR-GUARANTEE", "机场高速匝道站", "双向", "机场保障车队专线", 1, "fixed", amount=10.0, note="保障车队固定价"),
            ExceptionRoute("EX-GREEN-RETURN", "沪杭高速新桥站", "出城", "绿色冷链返程专线", 2, "discount", amount=0.8, note="返程空车8折"),
            ExceptionRoute("EX-AIR-EXPO", "机场高速匝道站", "出城", "会展摆渡专线", 1, "exempt", note="展会期间摆渡免费"),
        ),
    ),
)


def policy_for_date(day: str) -> PolicyVersion:
    """按通行日期选择当时生效的口径；早于任何版本时回落到最早版本。"""
    ordered = sorted(POLICY_VERSIONS, key=lambda item: item.effective_from)
    for version in ordered:
        if version.in_effect(day):
            return version
    return ordered[0]


def current_policy(day: str | None = None) -> PolicyVersion:
    """当前生效口径（不传日期时取最新版本）。"""
    if day:
        return policy_for_date(day)
    return sorted(POLICY_VERSIONS, key=lambda item: item.effective_from)[-1]


def evaluate(station: str, direction: str, paid_amount: float, day: str) -> dict[str, Any]:
    """统一判定入口，返回车辆列表/过路明细/费用汇总共用的结论。

    命中顺序：先按例外路线优先级判定；再看金额阈值。
    多条同级例外同时命中且结论不一致 -> 待核，等业务确认凭证裁决。
    """
    policy = policy_for_date(day)
    threshold = policy.threshold_for(station, direction)
    standard = threshold.standard if threshold else None
    band = (threshold.lower, threshold.upper) if threshold else policy.default_band
    matched_exceptions = policy.exceptions_for(station, direction)

    basis: list[str] = [f"规则版本 {policy.version}（{policy.effective_from} 起生效）"]
    if threshold:
        basis.append(threshold.describe())
    else:
        basis.append(
            f"兜底口径·{station}·{direction} 无单列阈值，按全局区间[{band[0]:g},{band[1]:g}]元判定"
        )

    result: dict[str, Any] = {
        "规则版本": policy.version,
        "命中例外": [],
        "判定依据": basis,
        "应收金额": None,
    }

    if matched_exceptions:
        top_priority = min(item.priority for item in matched_exceptions)
        winners = [item for item in matched_exceptions if item.priority == top_priority]
        for item in matched_exceptions:
            basis.append(item.describe())
        result["命中例外"] = [item.code for item in matched_exceptions]

        payable_choices = {item.payable(standard or 0.0): item for item in winners}
        if len(winners) > 1 and len(payable_choices) > 1:
            # 先按例外路线优先级裁决后仍冲突：以业务确认凭证为准，先挂待核
            basis.append("多条同级例外路线同时命中且金额口径不一致，判定待核，以业务确认凭证为准")
            result.update({
                "判定结论": "待核",
                "异常标记": True,
                "应收金额": None,
                "异常原因": "例外路线冲突",
            })
            return result

        winner = winners[0]
        payable = winner.payable(standard or 0.0)
        result["应收金额"] = payable
        if abs(paid_amount - payable) <= 0.01:
            basis.append(f"实付{paid_amount:g}元与例外应收{payable:g}元一致，费用正常")
            result.update({"判定结论": "正常", "异常标记": False, "异常原因": None})
        else:
            basis.append(f"实付{paid_amount:g}元与例外应收{payable:g}元不符，判定待核")
            result.update({"判定结论": "待核", "异常标记": True, "异常原因": "金额与例外路线不符"})
        return result

    # 无例外命中：金额阈值口径
    result["应收金额"] = standard
    if band[0] <= paid_amount <= band[1]:
        basis.append(f"实付{paid_amount:g}元落在阈值区间内，费用正常")
        result.update({"判定结论": "正常", "异常标记": False, "异常原因": None})
    else:
        basis.append(f"实付{paid_amount:g}元超出阈值区间[{band[0]:g},{band[1]:g}]元，判定待核")
        result.update({"判定结论": "待核", "异常标记": True, "异常原因": "金额超出阈值"})
    return result
