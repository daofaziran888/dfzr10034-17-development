"""质量测量领域服务：一次评估窗口内对表/资产执行的质量指标检查记录。

每条质量测量（一次质量检查）携带：
- 检查编号（全局唯一，同一检查只能归属一个评估窗口，不能同时为两个窗口贡献权重）
- 所属表 / 评估窗口 / 检查类型 / 阈值 / 权重 / 实测值
- 证据状态：有证据、实际零分（检查执行了但未通过，得 0 分）、无证据（未采集，不贡献权重）
- 复测状态与复测结论（复测结论回写巡检待办 schedule_job）
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class QualityMeasureService(BaseModuleService):
    MODULE = "quality_measure"
    ENTITY = "质量测量"
    OPERATION_TERM = "测量"
    LIST_FIELDS = [
        "测量编号", "检查编号", "所属表", "评估窗口", "检查类型",
        "阈值下限", "阈值上限", "权重", "实测值", "证据状态", "测量时间", "复测状态",
    ]
    REQUIRED_FIELDS = ["所属表", "评估窗口", "检查类型"]
    STATUS_ORDER = ["待复测", "已复测", "已作废"]
    ACTION_RULES = {"提交复测": "已复测", "作废测量": "已作废"}
    NEGATIVE_ACTIONS = ["作废测量"]

    # ── 证据口径 ──────────────────────────────────────────────

    @staticmethod
    def evidence_state(measured_value: Any, low: float, high: float) -> str:
        """按原口径判定一条检查的证据状态。

        - 无证据：没有采集到实测值（空值），该检查不参与权重计算，与实际零分严格区分
        - 实际零分：检查执行了但实测值越界，检查得 0 分
        - 有证据：检查执行且实测值落在阈值区间内
        """
        if measured_value is None or str(measured_value).strip() == "":
            return "无证据"
        value = float(measured_value)
        if value < low or value > high:
            return "实际零分"
        return "有证据"

    @staticmethod
    def check_score(measured_value: Any, low: float, high: float) -> float | None:
        """检查得分：无证据返回 None（不贡献权重）；越界返回 0；区间内返回实测值。"""
        state = QualityMeasureService.evidence_state(measured_value, low, high)
        if state == "无证据":
            return None
        if state == "实际零分":
            return 0.0
        return round(float(measured_value), 1)

    def find_by_check_code(self, check_code: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("检查编号") == check_code and row.get("status") != "已作废":
                return row
        return None

    def window_measures(self, table_code: str, window: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if row.get("所属表") == table_code
            and row.get("评估窗口") == window
            and row.get("status") != "已作废"
        ]

    def _assert_window_guard(self, check_code: str, window: str) -> None:
        """同一检查编号不能同时为两个评估窗口贡献权重。"""
        existing = self.find_by_check_code(check_code)
        if existing is not None and existing.get("评估窗口") != window:
            raise ValueError(
                f"检查{check_code}已在窗口{existing.get('评估窗口')}贡献权重，"
                "同一检查不能同时为两个窗口贡献权重"
            )

    def _assert_weight_guard(self, table_code: str, window: str, weight: float,
                             exclude_check: str | None = None) -> None:
        """同一窗口内质量维度权重合计不得超过 1。"""
        used = sum(
            float(row.get("权重", 0))
            for row in self.window_measures(table_code, window)
            if row.get("检查编号") != exclude_check
        )
        if used + weight > 1.0 + 1e-9:
            raise ValueError(
                f"表{table_code}窗口{window}质量权重合计{used + weight:.2f}超过1.00"
            )

    # ── 写入 ──────────────────────────────────────────────────

    def create_measure(
        self,
        table_code: str,
        window: str,
        check_type: str,
        threshold_low: float,
        threshold_high: float,
        weight: float,
        measured_value: Any = "",
        *,
        check_code: str | None = None,
        measure_time: str | None = None,
        entry_kind: str = "常规登记",
        note: str = "",
    ) -> dict[str, Any]:
        """登记一条质量测量（一次质量指标检查记录）。"""
        low, high, w = float(threshold_low), float(threshold_high), float(weight)
        measure_id = store.next_id(self.MODULE)
        code = check_code or f"QMS-{measure_id:04d}"

        self._assert_window_guard(code, window)
        self._assert_weight_guard(table_code, window, w)

        state = self.evidence_state(measured_value, low, high)
        measure = {
            "id": measure_id,
            "测量编号": f"QMM-{measure_id:04d}",
            "检查编号": code,
            "所属表": table_code,
            "评估窗口": window,
            "检查类型": check_type,
            "阈值下限": str(low),
            "阈值上限": str(high),
            "权重": str(w),
            "实测值": "" if measured_value is None or str(measured_value).strip() == "" else str(measured_value),
            "证据状态": state,
            "测量时间": measure_time or _time.strftime("%Y-%m-%d %H:%M:%S"),
            "录入类型": entry_kind,
            "备注": note,
            "复测状态": "待复测",
            "复测值": "",
            "复测结论": "",
            "复测人": "",
            "复测时间": "",
            "关联待办": "",
            "status": "待复测",
            "pending": True,
            "abnormal": state == "实际零分",
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(measure)
        return measure

    def register_late_measure(self, table_code: str, window: str, **kwargs: Any) -> dict[str, Any]:
        """评分签发后补登记的质量测量：标记补登记，不改变已签发评分的原始依据。"""
        kwargs.setdefault("entry_kind", "补登记")
        kwargs.setdefault("note", "评分签发后补登记，仅对之后窗口/重算可见")
        return self.create_measure(table_code, window, **kwargs)

    # ── 复测 ──────────────────────────────────────────────────

    def retest_measure(
        self, measure_id: int, new_value: float, conclusion: str, reviewer: str
    ) -> dict[str, Any]:
        """质量规则复测：复测结论回写巡检待办，原始检查记录保留。"""
        measure = self.get_entry(measure_id)
        if measure is None:
            return {"ok": False, "error": "质量测量不存在"}
        if measure.get("status") == "已作废":
            return {"ok": False, "error": "已作废测量不能复测"}

        low = float(measure["阈值下限"])
        high = float(measure["阈值上限"])
        retest_state = self.evidence_state(new_value, low, high)

        measure["复测值"] = str(new_value)
        measure["复测结论"] = conclusion
        measure["复测人"] = reviewer
        measure["复测时间"] = _time.strftime("%Y-%m-%d %H:%M:%S")
        measure["复测状态"] = "已复测"
        measure["复测证据状态"] = retest_state
        measure["status"] = "已复测"
        measure["pending"] = False

        todo = self._writeback_inspection_todo(measure, new_value, retest_state, conclusion, reviewer)
        measure["关联待办"] = todo["作业编号"]

        store.log_change(
            self.MODULE, measure_id, "质量规则复测",
            before=f"实测值{measure.get('实测值') or '空'}", after=f"复测值{new_value}/{retest_state}",
            source="retest", scope="quality_measure->schedule_job"
        )
        self.emit_event("quality_measure_retested", {
            "measure_id": measure_id,
            "check_code": measure.get("检查编号"),
            "retest_state": retest_state,
            "todo_code": todo["作业编号"],
        })
        return {"ok": True, "measure": measure, "inspection_todo": todo}

    def _writeback_inspection_todo(
        self, measure: dict[str, Any], new_value: float, retest_state: str,
        conclusion: str, reviewer: str
    ) -> dict[str, Any]:
        """把复测结论回写到巡检待办（schedule_job）；已有待办则更新，否则新建。"""
        check_code = measure.get("检查编号", "")
        existing = next(
            (row for row in store.rows("schedule_job")
             if row.get("关联检查编号") == check_code and row.get("治理类型") == "质量巡检待办"),
            None,
        )
        if existing is not None:
            existing["复测结论"] = conclusion
            existing["复测值"] = str(new_value)
            existing["复测证据状态"] = retest_state
            existing["复测人"] = reviewer
            existing["复测时间"] = _time.strftime("%Y-%m-%d %H:%M:%S")
            existing["巡检状态"] = "复测已回写"
            existing["status"] = "已完成"
            existing["pending"] = False
            return existing

        job_id = store.next_id("schedule_job")
        todo = {
            "id": job_id,
            "作业编号": f"SCHE-{job_id:04d}",
            "治理类型": "质量巡检待办",
            "目标资产": measure.get("所属表", ""),
            "Cron表达式": "手动触发",
            "依赖检查": f"检查编号{check_code}（{measure.get('检查类型', '')}）",
            "超时上限": "30min",
            "失败通知": "data-quality@example.com",
            "下次执行": measure.get("测量时间", ""),
            "关联检查编号": check_code,
            "评估窗口": measure.get("评估窗口", ""),
            "实测值": measure.get("实测值", ""),
            "复测值": str(new_value),
            "复测证据状态": retest_state,
            "复测结论": conclusion,
            "复测人": reviewer,
            "复测时间": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "巡检状态": "复测已回写",
            "status": "已完成",
            "pending": False,
            "abnormal": retest_state == "实际零分",
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows("schedule_job").append(todo)
        return todo
