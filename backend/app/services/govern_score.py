"""治理评分领域服务：多维度治理健康评分、改进跟踪和整改闭环。

接收血缘追踪、标签传播、变更捕获等各上游模块的数据作为评分维度。

本模块在原综合评分之外，提供面向评审的「治理评分贡献视图」：
- 一次评估按质量指标检查记录（quality_measure）和权重计算每项贡献；
- 每个总分都记录签发时的证据快照（score_breakdown），评审可追溯每个分数用了哪份证据；
- 无证据（未采集，不贡献权重）与实际零分（检查执行但越界，得 0 分）严格分开；
- 同一检查编号只能归属一个评估窗口，不能同时为两个窗口贡献权重（窗口守卫在质量测量服务）；
- 按原口径重算历史评分兼容已有评分：无快照的旧评分走旧规则，带快照的评分按快照重算；
- 补登记/补录检查不改变已签发评分的原始分项依据，只进入下一轮复测或新窗口；
- 复测结论回写巡检待办（schedule_job），最初评分的分项依据原样保留。
"""
from __future__ import annotations

import time as _time
from typing import Any

from app.services.base import BaseModuleService
from app.services.quality_measure import QualityMeasureService
from app.store import store

QUALITY_DIMENSION = "质量监控覆盖"


class GovernScoreService(BaseModuleService):
    MODULE = "govern_score"
    ENTITY = "评分任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务编号", "评估对象", "评分维度", "当前分数", "改进基线", "评估周期", "预警级别", "整改状态"]
    REQUIRED_FIELDS = ["任务编号", "评估对象", "评分维度"]
    STATUS_ORDER = ["待评估", "评估中", "已出分", "整改中"]
    ACTION_RULES = {"发起评估": "评估中", "确认评分": "已出分", "指派整改": "整改中"}
    NEGATIVE_ACTIONS = ["指派整改"]

    SCORE_DIMENSIONS = {
        "元数据完备性": {"base": 100, "field": "metadata", "weight": 0.15},
        "质量监控覆盖": {"base": 80, "field": "quality", "weight": 0.20},
        "分级标签准确度": {"base": 70, "field": "classification", "weight": 0.15},
        "血缘完备性": {"base": 60, "field": "lineage", "weight": 0.20},
        "标签覆盖": {"base": 50, "field": "tag", "weight": 0.10},
        "契约合规": {"base": 40, "field": "contract", "weight": 0.10},
        "负责人到位": {"base": 30, "field": "owner", "weight": 0.10},
    }

    def __init__(self) -> None:
        self.qms = QualityMeasureService()

    # ══════════════════════════════════════════════════════════════
    # 原有综合评分（保持原口径，供 Pipeline 7 与历史评分兼容使用）
    # ══════════════════════════════════════════════════════════════

    def evaluate_asset(self, asset_code: str) -> dict[str, Any]:
        """对资产进行多维度治理评分（Pipeline 7 汇总输出）。"""
        dimensions = self._legacy_dimension_scores(asset_code)
        overall = self._weighted_overall(dimensions)

        # 保存评分记录
        score_id = store.next_id(self.MODULE)
        score_entry = {
            "id": score_id,
            "任务编号": f"GOVE-{score_id:04d}",
            "评估对象": asset_code,
            "评分维度": "综合评估",
            "当前分数": str(overall),
            "改进基线": self._compute_baseline(dimensions, overall),
            "评估周期": "按需",
            "预警级别": self._severity_label(overall),
            "整改状态": "已出分",
            "status": "已出分",
            "pending": overall < 60,
            "abnormal": overall < 40,
            "dimensions": dimensions,
            "scoring_basis": "legacy",
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(score_entry)

        self.emit_event("governance_scored", {
            "score_id": score_id,
            "asset_code": asset_code,
            "overall": overall,
            "dimensions": dimensions,
        })

        return {
            "score_id": score_id,
            "asset": asset_code,
            "overall": overall,
            "dimensions": dimensions,
            "pipeline": "lineage_track->impact_analysis->govern_score",
        }

    def _legacy_dimension_scores(self, asset_code: str) -> dict[str, int]:
        """旧口径：基于各模块登记数量推导七个维度分（无质量测量证据的概念）。"""
        dimensions: dict[str, int] = {}

        metadata = store.find_by_field("metadata_index", "所属资产", asset_code)
        dimensions["元数据完备性"] = min(100, len(metadata) * 20)

        quality = store.find_by_field("quality_rule", "目标资产", asset_code)
        dimensions["质量监控覆盖"] = min(100, len(quality) * 33)

        classifications = [
            c for c in store.rows("classification")
            if str(c.get("目标字段", "")).startswith(f"{asset_code}.")
        ]
        dimensions["分级标签准确度"] = min(
            100, len([c for c in classifications if c.get("status") == "已确认"]) * 25)

        lineage = [
            l for l in store.rows("lineage_track")
            if l.get("上游资产") == asset_code or l.get("下游资产") == asset_code
        ]
        dimensions["血缘完备性"] = min(100, len(lineage) * 25)

        tag_assets = [t.get("关联资产数", "0") for t in store.rows("tag_registry")]
        dimensions["标签覆盖"] = min(100, 50 + len(tag_assets) * 5)

        contracts = store.find_by_field("data_contract", "数据资产", asset_code)
        dimensions["契约合规"] = min(100, len([c for c in contracts if c.get("status") == "已签署"]) * 50)

        owners = store.find_by_field("owner_mgr", "目标资产", asset_code)
        dimensions["负责人到位"] = min(100, len([o for o in owners if o.get("status") == "已生效"]) * 50)
        return dimensions

    def _weighted_overall(self, dimension_scores: dict[str, float | None]) -> float | None:
        """加权总分：无证据（None）维度不参与权重，按剩余权重重归一化。"""
        total = 0.0
        weight_used = 0.0
        for dim, cfg in self.SCORE_DIMENSIONS.items():
            score = dimension_scores.get(dim)
            if score is None:
                continue
            total += float(score) * cfg["weight"]
            weight_used += cfg["weight"]
        if weight_used <= 0:
            return None
        return round(total / weight_used, 1)

    # ══════════════════════════════════════════════════════════════
    # 证据化评分 + 贡献视图
    # ══════════════════════════════════════════════════════════════

    def _quality_contribution(self, table_code: str, window: str) -> dict[str, Any]:
        """汇总一个评估窗口内质量维度的检查贡献。

        无证据与实际零分分开：
        - 无证据检查：check_score 为 None，不计入权重，也不把质量维度拉成 0 分；
        - 实际零分检查：check_score = 0，按其权重真实拉低质量维度分。
        """
        measures = self.qms.window_measures(table_code, window)
        contributors: list[dict[str, Any]] = []
        weighted_sum = 0.0
        weight_covered = 0.0
        has_zero = False
        has_no_evidence = False

        for measure in measures:
            low = float(measure["阈值下限"])
            high = float(measure["阈值上限"])
            weight = float(measure["权重"])
            score = QualityMeasureService.check_score(measure.get("实测值", ""), low, high)
            state = measure.get("证据状态", QualityMeasureService.evidence_state(
                measure.get("实测值", ""), low, high))
            if state == "实际零分":
                has_zero = True
            if state == "无证据":
                has_no_evidence = True
            if score is not None:
                weighted_sum += score * weight
                weight_covered += weight
            contributors.append({
                "check_code": measure["检查编号"],
                "measure_id": measure["id"],
                "check_type": measure["检查类型"],
                "weight": weight,
                "measured_value": measure.get("实测值", ""),
                "threshold": [low, high],
                "check_score": score,
                "evidence_state": state,
                "retest_state": measure.get("复测状态", "待复测"),
                "entry_kind": measure.get("录入类型", "常规登记"),
            })

        if weight_covered <= 0:
            quality_score: float | None = None
            evidence = "无证据"
        else:
            quality_score = round(weighted_sum / weight_covered, 1)
            if has_zero:
                evidence = "含实际零分"
            elif has_no_evidence:
                evidence = "部分无证据"
            else:
                evidence = "有证据"

        return {
            "dimension": QUALITY_DIMENSION,
            "weight": self.SCORE_DIMENSIONS[QUALITY_DIMENSION]["weight"],
            "score": quality_score,
            "evidence": evidence,
            "weight_covered": round(weight_covered, 4),
            "contributors": contributors,
        }

    def _build_breakdown(
        self, table_code: str, window: str, dimension_scores: dict[str, float] | None
    ) -> dict[str, Any]:
        """构建签发时的证据快照：每个维度的得分、权重、贡献与原始检查。"""
        provided = dimension_scores or {}
        legacy = self._legacy_dimension_scores(table_code)
        dimensions: list[dict[str, Any]] = []

        for dim, cfg in self.SCORE_DIMENSIONS.items():
            if dim == QUALITY_DIMENSION:
                detail = self._quality_contribution(table_code, window)
                score = detail["score"]
                detail["weighted_contribution"] = (
                    round(score * cfg["weight"], 4) if score is not None else 0.0
                )
                dimensions.append(detail)
                continue

            if dim in provided:
                score = float(provided[dim])
                evidence = "显式提供"
            else:
                score = float(legacy.get(dim, 0))
                evidence = "规则计算"
            dimensions.append({
                "dimension": dim,
                "weight": cfg["weight"],
                "score": score,
                "evidence": evidence,
                "weighted_contribution": round(score * cfg["weight"], 4),
                "contributors": [],
            })

        score_map = {item["dimension"]: item["score"] for item in dimensions}
        overall = self._weighted_overall(score_map)
        weight_used = round(sum(
            item["weight"] for item in dimensions if item["score"] is not None
        ), 4)
        return {
            "window": window,
            "table_code": table_code,
            "issued_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "overall": overall,
            "weight_used": weight_used,
            "dimensions": dimensions,
        }

    def issue_with_evidence(
        self, table_code: str, window: str,
        dimension_scores: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """按一次评估窗口的质量检查与权重签发评分，并固化证据快照。"""
        existing = self._find_score(table_code, window)
        if existing is not None:
            return {
                "ok": False,
                "error": f"{table_code} 在窗口{window}已签发评分{existing['任务编号']}，不能重复签发",
                "score_id": existing["id"],
            }

        breakdown = self._build_breakdown(table_code, window, dimension_scores)
        overall = breakdown["overall"]
        score_id = store.next_id(self.MODULE)
        entry = {
            "id": score_id,
            "任务编号": f"GOVE-{score_id:04d}",
            "评估对象": table_code,
            "评分维度": "综合评估（证据化）",
            "当前分数": str(overall if overall is not None else "无证据"),
            "改进基线": self._breakdown_baseline(breakdown),
            "评估周期": window,
            "预警级别": self._severity_label(overall) if overall is not None else "无证据",
            "整改状态": "已出分",
            "status": "已出分",
            "pending": (overall is None) or overall < 60,
            "abnormal": overall is not None and overall < 40,
            "dimensions": {
                item["dimension"]: item["score"] for item in breakdown["dimensions"]
            },
            "评估窗口": window,
            "score_breakdown": breakdown,
            "scoring_basis": "evidence_v1",
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(self.MODULE).append(entry)

        for module in ("survey_week", "payout_week"):
            for row in store.rows(module):
                if row.get("表编号") == table_code and row.get("周次") == window:
                    row["status"] = "已评分"
                    row["质量测量条数"] = str(len(
                        self.qms.window_measures(table_code, window)))

        self.emit_event("governance_scored", {
            "score_id": score_id,
            "asset_code": table_code,
            "window": window,
            "overall": overall,
            "basis": "evidence_v1",
        })
        return {
            "ok": True,
            "score_id": score_id,
            "asset": table_code,
            "window": window,
            "overall": overall,
            "breakdown": breakdown,
        }

    def _breakdown_baseline(self, breakdown: dict[str, Any]) -> str:
        weak: list[str] = []
        for item in breakdown["dimensions"]:
            if item["score"] is None:
                weak.append(f"{item['dimension']}缺证据")
            elif item["score"] < 60:
                weak.append(f"{item['dimension']}{item['score']}分")
        if weak:
            return "补强：" + "、".join(weak[:2])
        overall = breakdown.get("overall")
        if overall is not None and overall < 80:
            return "各维度持续改进以达到优良水平"
        return "维持当前水平"

    # ══════════════════════════════════════════════════════════════
    # 贡献视图查询
    # ══════════════════════════════════════════════════════════════

    def _find_score(self, table_code: str | None = None,
                    window: str | None = None, score_id: int | None = None) -> dict[str, Any] | None:
        if score_id is not None:
            return store.find(self.MODULE, score_id)
        rows = store.rows(self.MODULE)
        if table_code:
            rows = [r for r in rows if r.get("评估对象") == table_code]
        if window:
            rows = [r for r in rows if r.get("评估窗口") == window or r.get("评估周期") == window]
        return rows[-1] if rows else None

    def _window_rankings(self, window: str) -> dict[int, dict[str, Any]]:
        """同一窗口内：历史排名按总分降序；本周排查次序按待复测/零分证据量降序。"""
        rows = [
            r for r in store.rows(self.MODULE)
            if (r.get("评估窗口") or r.get("评估周期")) == window
        ]
        historical = sorted(rows, key=lambda r: float(r.get("当前分数") or 0), reverse=True)

        def triage_key(row: dict[str, Any]) -> tuple[int, int, float]:
            # 本周排查次序：待复测/零分检查越多越靠前；并列时看无证据检查数，再比总分
            pending = len(self.next_retest_items(row))
            zero = self._count_zero_checks(row)
            no_evidence = self._count_no_evidence_checks(row)
            overall = float(row.get("当前分数") or 0)
            return (pending + zero, no_evidence, -overall)

        triage = sorted(rows, key=triage_key, reverse=True)
        result: dict[int, dict[str, Any]] = {}
        for idx, row in enumerate(historical, start=1):
            result.setdefault(row["id"], {})["historical_rank"] = idx
        for idx, row in enumerate(triage, start=1):
            result.setdefault(row["id"], {})["inspection_order"] = idx
        for info in result.values():
            info["order_conflict"] = info["historical_rank"] != info["inspection_order"]
        return result

    def _count_zero_checks(self, score_row: dict[str, Any]) -> int:
        breakdown = score_row.get("score_breakdown")
        if not breakdown:
            return 0
        return sum(
            1 for dim in breakdown["dimensions"]
            for c in dim.get("contributors", [])
            if c.get("evidence_state") == "实际零分"
        )

    def _count_no_evidence_checks(self, score_row: dict[str, Any]) -> int:
        breakdown = score_row.get("score_breakdown")
        if not breakdown:
            return 0
        return sum(
            1 for dim in breakdown["dimensions"]
            for c in dim.get("contributors", [])
            if c.get("evidence_state") == "无证据"
        )

    def next_retest_items(self, score_row: dict[str, Any]) -> list[dict[str, Any]]:
        """下一轮复测项目：该表该窗口内复测状态为待复测的检查（含补录检查）。"""
        window = score_row.get("评估窗口") or score_row.get("评估周期") or ""
        table = score_row.get("评估对象", "")
        items = []
        for measure in self.qms.window_measures(table, window):
            if measure.get("复测状态") == "待复测":
                items.append({
                    "measure_id": measure["id"],
                    "check_code": measure["检查编号"],
                    "check_type": measure["检查类型"],
                    "evidence_state": measure["证据状态"],
                    "entry_kind": measure.get("录入类型", "常规登记"),
                    "measured_value": measure.get("实测值", ""),
                })
        # 实际零分优先，再按补录/常规顺序
        items.sort(key=lambda x: (x["evidence_state"] != "实际零分", x["entry_kind"] != "补录检查"))
        return items

    def get_contribution_view(self, score_id: int | None = None,
                              table_code: str | None = None,
                              window: str | None = None) -> dict[str, Any]:
        """治理评分贡献视图：列出影响总分的原始检查与每项贡献。"""
        score_row = self._find_score(table_code, window, score_id)
        if score_row is None:
            return {"ok": False, "error": "未找到匹配的评分记录"}

        breakdown = score_row.get("score_breakdown")
        view_window = score_row.get("评估窗口") or score_row.get("评估周期") or ""
        rankings = self._window_rankings(view_window).get(score_row["id"], {})

        if breakdown is None:
            # 无证据快照的历史评分：按旧口径展示，明确标注证据基础不同
            legacy_dims = self._legacy_dimension_scores(score_row["评估对象"])
            dimensions = [
                {
                    "dimension": dim,
                    "weight": cfg["weight"],
                    "score": legacy_dims.get(dim),
                    "evidence": "旧口径（无检查证据快照）",
                    "weighted_contribution": round(legacy_dims.get(dim, 0) * cfg["weight"], 4),
                    "contributors": [],
                }
                for dim, cfg in self.SCORE_DIMENSIONS.items()
            ]
            overall = self._weighted_overall(legacy_dims)
            breakdown = {
                "window": view_window,
                "table_code": score_row["评估对象"],
                "issued_at": score_row.get("created_at", ""),
                "overall": overall,
                "weight_used": round(sum(cfg["weight"] for cfg in self.SCORE_DIMENSIONS.values()), 4),
                "dimensions": dimensions,
            }

        quality_detail = next(
            (d for d in breakdown["dimensions"] if d["dimension"] == QUALITY_DIMENSION), None)
        no_evidence_checks = [
            c for d in breakdown["dimensions"]
            for c in d.get("contributors", [])
            if c.get("evidence_state") == "无证据"
        ]
        zero_checks = [
            c for d in breakdown["dimensions"]
            for c in d.get("contributors", [])
            if c.get("evidence_state") == "实际零分"
        ]

        return {
            "ok": True,
            "score": {
                "score_id": score_row["id"],
                "task_code": score_row["任务编号"],
                "table_code": score_row["评估对象"],
                "window": view_window,
                "overall": float(score_row["当前分数"]) if str(score_row["当前分数"]).replace(".", "").isdigit() else None,
                "issued_at": breakdown.get("issued_at"),
                "scoring_basis": score_row.get("scoring_basis", "legacy"),
                "warning_level": score_row.get("预警级别"),
            },
            "breakdown": breakdown,
            "quality_evidence_summary": {
                "evidence": quality_detail["evidence"] if quality_detail else "无证据",
                "quality_score": quality_detail["score"] if quality_detail else None,
                "no_evidence_checks": no_evidence_checks,
                "actual_zero_checks": zero_checks,
                "raw_check_count": len(quality_detail["contributors"]) if quality_detail else 0,
            },
            "next_retest_items": self.next_retest_items(score_row),
            "ranking": rankings,
            "last_recompute": score_row.get("last_recompute"),
        }

    # ══════════════════════════════════════════════════════════════
    # 按原口径重算历史评分（兼容已有评分）
    # ══════════════════════════════════════════════════════════════

    def recompute_score(self, score_id: int) -> dict[str, Any]:
        """按原口径重算：旧评分走旧规则，证据化评分按签发快照重算，并报告补登记漂移。"""
        score_row = store.find(self.MODULE, score_id)
        if score_row is None:
            return {"ok": False, "error": "评分记录不存在"}

        original = score_row.get("当前分数")
        basis = score_row.get("scoring_basis", "legacy")
        breakdown = score_row.get("score_breakdown")

        if breakdown is None:
            dims = self._legacy_dimension_scores(score_row["评估对象"])
            recomputed = self._weighted_overall(dims)
            evidence_drift: list[dict[str, Any]] = []
        else:
            snapshot_scores = {
                item["dimension"]: item["score"] for item in breakdown["dimensions"]
            }
            recomputed = self._weighted_overall(snapshot_scores)
            snapshot_codes = {
                c["check_code"] for d in breakdown["dimensions"]
                for c in d.get("contributors", [])
            }
            current = self.qms.window_measures(
                score_row["评估对象"], breakdown["window"])
            evidence_drift = [
                {
                    "check_code": m["检查编号"],
                    "entry_kind": m.get("录入类型", "常规登记"),
                    "evidence_state": m["证据状态"],
                    "note": "签发后补登记，未计入原始评分",
                }
                for m in current if m["检查编号"] not in snapshot_codes
            ]

        compatible = str(recomputed) == str(original) or (
            str(original).replace(".", "").isdigit()
            and abs(float(original) - (recomputed or 0)) < 0.05
        )
        result = {
            "recomputed_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "basis": "legacy_rule" if breakdown is None else "evidence_snapshot",
            "original_score": original,
            "recomputed_score": recomputed,
            "compatible": compatible,
            "late_registered_evidence": evidence_drift,
        }
        score_row["last_recompute"] = result
        return {"ok": True, **result}

    # ══════════════════════════════════════════════════════════════
    # 补登记 / 补录检查 / 复测
    # ══════════════════════════════════════════════════════════════

    def register_late_measurement(self, table_code: str, window: str, **kwargs: Any) -> dict[str, Any]:
        """评分签发后补登记一次质量测量。

        窗口守卫保证同一检查不能同时为两个窗口贡献权重；已签发评分的原始依据不变，
        补登记证据只在重算漂移和下一轮复测中提示。
        """
        try:
            measure = self.qms.register_late_measure(table_code, window, **kwargs)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        locked_score = self._find_score(table_code, window)
        conflict = None
        if locked_score is not None:
            rankings = self._window_rankings(window).get(locked_score["id"], {})
            conflict = {
                "locked_task": locked_score["任务编号"],
                "locked_score": locked_score["当前分数"],
                "historical_rank": rankings.get("historical_rank"),
                "inspection_order": rankings.get("inspection_order"),
                "order_conflict": rankings.get("order_conflict"),
                "note": "补登记证据不改变已签发评分；评审以贡献视图中的证据快照为准",
            }
        return {"ok": True, "measure": measure, "locked_score": conflict}

    def add_supplemental_measure(self, table_code: str, window: str, **kwargs: Any) -> dict[str, Any]:
        """为总分接近的表补录一次质量检查，重新进入贡献视图的下一轮复测清单。"""
        kwargs.setdefault("entry_kind", "补录检查")
        kwargs.setdefault("note", "评审补录检查，待质量规则复测")
        try:
            measure = self.qms.create_measure(table_code, window, **kwargs)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        measure["复测状态"] = "待复测"
        measure["status"] = "待复测"
        measure["pending"] = True
        return {"ok": True, "measure": measure}

    def retest_measure(self, measure_id: int, new_value: float,
                       conclusion: str, reviewer: str) -> dict[str, Any]:
        """复测质量规则：结论回写巡检待办，最初评分的分项依据保留不变。"""
        measure = self.qms.get_entry(measure_id)
        if measure is None:
            return {"ok": False, "error": "质量测量不存在"}
        result = self.qms.retest_measure(measure_id, new_value, conclusion, reviewer)
        if not result.get("ok"):
            return result
        # 已签发评分的快照不重写；返回该表该窗口仍待复测的项目
        table_code = measure["所属表"]
        window = measure["评估窗口"]
        score_row = self._find_score(table_code, window)
        return {
            "ok": True,
            "measure": result["measure"],
            "inspection_todo": result["inspection_todo"],
            "original_breakdown_preserved": score_row is not None,
            "remaining_retest_items": (
                self.next_retest_items(score_row) if score_row is not None else []
            ),
        }

    # ══════════════════════════════════════════════════════════════

    def _compute_baseline(self, dims: dict[str, int], overall: float) -> str:
        """计算改进基线。"""
        weaknesses = [(d, s) for d, s in dims.items() if s < 60]
        if weaknesses:
            weakest = min(weaknesses, key=lambda x: x[1])
            return f"加强{weakest[0]}（当前{weakest[1]}分）"
        if overall < 80:
            return "各维度持续改进以达到优良水平"
        return "维持当前水平"

    def _severity_label(self, score: float) -> str:
        if score >= 80:
            return "正常"
        elif score >= 60:
            return "需关注"
        elif score >= 40:
            return "严重"
        return "危急"

    def get_scores_by_asset(self, asset_code: str) -> list[dict[str, Any]]:
        """获取资产的所有治理评分记录。"""
        return store.find_by_field(self.MODULE, "评估对象", asset_code)
