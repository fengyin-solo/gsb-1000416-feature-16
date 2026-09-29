"""通行费用统一判定口径。

金额阈值（按版本生效期管理）、例外路线（带优先级）和判定依据全部集中在这一处。
车辆列表、过路明细、费用汇总只能通过 ``TollPolicy.evaluate`` 读取结论，
不允许各自再维护一套阈值或例外判断，避免同一条过路记录在不同页面读出不同结果。

历史费用按当时规则留痕：判定时按通行日期回溯当时生效的阈值版本，
规则改版不会改变历史记录的判定结论（结论本身在写入时已落入判定历史）。
"""
from __future__ import annotations

from typing import Any

from app.store import store

POLICY_MODULE = "toll_policy_versions"
EXCEPTION_MODULE = "toll_exception_routes"

# 例外路线里用这个方向表示双向都适用
DIRECTION_WILDCARD = "双向"
# 例外规则适用版本为该值时，对所有阈值版本生效
VERSION_WILDCARD = "通用"

CONCLUSION_NORMAL = "正常"
CONCLUSION_EXCEPTION = "例外放行"
CONCLUSION_ABNORMAL = "异常"


class TollPolicy:
    """无状态判定器：所有口径数据存放在 store 的两张口径表里。"""

    def versions(self) -> list[dict[str, Any]]:
        return store.rows(POLICY_MODULE)

    def exception_rules(self) -> list[dict[str, Any]]:
        return store.rows(EXCEPTION_MODULE)

    def effective_version(self, on_date: str | None) -> dict[str, Any] | None:
        """按通行日期取当时生效的阈值版本；同一天多版本时取生效日最晚的一个。"""
        candidates: list[dict[str, Any]] = []
        for version in self.versions():
            start = str(version.get("生效日期") or "")
            end = version.get("失效日期")
            if on_date and on_date < start:
                continue
            if end and on_date and on_date > str(end):
                continue
            candidates.append(version)
        if not candidates:
            return None
        return max(candidates, key=lambda item: str(item.get("生效日期") or ""))

    def current_version(self) -> dict[str, Any] | None:
        from datetime import date

        return self.effective_version(date.today().isoformat())

    def _matching_exceptions(
        self,
        version_code: str | None,
        station: str,
        direction: str,
        amount: float,
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for rule in self.exception_rules():
            if not rule.get("启用", True):
                continue
            applies = rule.get("适用版本")
            if applies not in (VERSION_WILDCARD, version_code):
                continue
            if str(rule.get("收费站名称") or "").strip() != station:
                continue
            rule_direction = str(rule.get("通行方向") or "").strip()
            if rule_direction != DIRECTION_WILDCARD and rule_direction != direction:
                continue
            lower = rule.get("金额下限")
            upper = rule.get("金额上限")
            if lower is not None and amount < float(lower):
                continue
            if upper is not None and amount > float(upper):
                continue
            hits.append(rule)
        hits.sort(key=lambda item: int(item.get("优先级", 0)), reverse=True)
        return hits

    def evaluate(
        self,
        *,
        station: str,
        direction: str,
        amount_raw: Any,
        on_date: str | None,
    ) -> dict[str, Any]:
        """对一次过路事实给出唯一结论。

        返回口径快照：判定结论 / 异常类型 / 判定依据 / 适用规则版本 / 命中例外。
        多条例外同时命中时先按优先级取最高档；最高档内结论仍冲突的，
        判为异常待核，以业务确认凭证为准。
        """
        version = self.effective_version(on_date)
        version_code = str(version.get("版本")) if version else "未配置"
        lower = float(version.get("金额下限", 0)) if version else 0.0
        upper = float(version.get("金额上限", 0)) if version else 0.0

        try:
            amount = float(str(amount_raw).strip())
        except (TypeError, ValueError):
            return {
                "判定结论": CONCLUSION_ABNORMAL,
                "异常类型": "金额无法识别",
                "判定依据": f"收费金额「{amount_raw}」无法识别，需核对收费凭证",
                "适用规则版本": version_code,
                "命中例外": [],
            }

        hits = self._matching_exceptions(version_code, station.strip(), direction.strip(), amount)
        if hits:
            top_priority = int(hits[0].get("优先级", 0))
            top_hits = [rule for rule in hits if int(rule.get("优先级", 0)) == top_priority]
            verdicts = {str(rule.get("处置结论") or "").strip() for rule in top_hits}
            names = "、".join(str(rule.get("规则名称")) for rule in top_hits)
            if len(verdicts) > 1:
                return {
                    "判定结论": CONCLUSION_ABNORMAL,
                    "异常类型": "例外规则冲突",
                    "判定依据": (
                        f"收费站「{station}」{direction} 同时命中 {len(top_hits)} 条优先级 "
                        f"{top_priority} 的例外路线（{names}），处置结论冲突；"
                        "按口径标记待核，以业务确认凭证为准"
                    ),
                    "适用规则版本": version_code,
                    "命中例外": [rule.get("规则名称") for rule in top_hits],
                }
            rule = top_hits[0]
            verdict = str(rule.get("处置结论") or "").strip()
            if verdict == CONCLUSION_EXCEPTION:
                return {
                    "判定结论": CONCLUSION_EXCEPTION,
                    "异常类型": "",
                    "判定依据": (
                        f"命中优先级 {top_priority} 的例外路线「{rule.get('规则名称')}」"
                        f"（{rule.get('依据说明')}）；收费金额 {amount:.2f} 元按例外口径放行，"
                        f"不按 {version_code} 阈值 {lower:.2f}~{upper:.2f} 元判异"
                    ),
                    "适用规则版本": version_code,
                    "命中例外": [rule.get("规则名称")],
                }
            # 例外规则明确写的是「按标准」：落回阈值判定，但依据里要留痕
            fallback_note = f"例外路线「{rule.get('规则名称')}」要求按标准阈值判定；"
        else:
            fallback_note = ""

        if amount < lower:
            return {
                "判定结论": CONCLUSION_ABNORMAL,
                "异常类型": "金额低于阈值",
                "判定依据": (
                    f"{fallback_note}收费金额 {amount:.2f} 元低于 {version_code} 下限 "
                    f"{lower:.2f} 元，差额 {lower - amount:.2f} 元，疑似漏收/录入错误，标记待核"
                ),
                "适用规则版本": version_code,
                "命中例外": [],
            }
        if amount > upper:
            return {
                "判定结论": CONCLUSION_ABNORMAL,
                "异常类型": "金额高于阈值",
                "判定依据": (
                    f"{fallback_note}收费金额 {amount:.2f} 元高于 {version_code} 上限 "
                    f"{upper:.2f} 元，超额 {amount - upper:.2f} 元且无例外路线覆盖，标记待核"
                ),
                "适用规则版本": version_code,
                "命中例外": [],
            }
        return {
            "判定结论": CONCLUSION_NORMAL,
            "异常类型": "",
            "判定依据": (
                f"{fallback_note}收费金额 {amount:.2f} 元处于 {version_code} 阈值区间 "
                f"{lower:.2f}~{upper:.2f} 元，符合标准"
            ),
            "适用规则版本": version_code,
            "命中例外": [],
        }


policy = TollPolicy()
