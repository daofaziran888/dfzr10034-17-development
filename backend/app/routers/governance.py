"""治理评分贡献视图路由：证据化签发、贡献明细、原口径重算、补登记/补录/复测。

固定路径，必须在通用资源路由 resource 之前注册，避免被 /api/{module} 抢占。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.govern_score import GovernScoreService

router = APIRouter(prefix="/api/governance-scoring", tags=["治理评分贡献视图"])

_service = GovernScoreService()


class IssuePayload(BaseModel):
    table_code: str
    window: str
    dimension_scores: dict[str, float] | None = None


class LateMeasurePayload(BaseModel):
    table_code: str
    window: str
    check_type: str
    threshold_low: float
    threshold_high: float
    weight: float
    measured_value: Any = ""
    check_code: str | None = None
    measure_time: str | None = None
    note: str | None = None


class SupplementPayload(LateMeasurePayload):
    pass


class RetestPayload(BaseModel):
    new_value: float
    conclusion: str
    reviewer: str


@router.post("/issues")
def issue_score(payload: IssuePayload) -> dict[str, Any]:
    """按一次评估窗口的质量检查与权重签发评分。"""
    return _service.issue_with_evidence(
        payload.table_code, payload.window, payload.dimension_scores)


@router.get("/contributions")
def contribution_view(score_id: int | None = None, table_code: str | None = None,
                      window: str | None = None) -> dict[str, Any]:
    """贡献视图：列出每个总分关联的检查明细、无证据/零分区分与复测项目。"""
    return _service.get_contribution_view(score_id, table_code, window)


@router.post("/scores/{score_id}/recompute")
def recompute_score(score_id: int) -> dict[str, Any]:
    """按原口径重算历史评分，兼容已有评分并报告补登记证据漂移。"""
    return _service.recompute_score(score_id)


@router.post("/measures/late")
def register_late_measurement(payload: LateMeasurePayload) -> dict[str, Any]:
    """评分签发后补登记一次质量测量。"""
    data = payload.model_dump(exclude_none=True)
    return _service.register_late_measurement(
        data.pop("table_code"), data.pop("window"), **data)


@router.post("/measures/supplement")
def add_supplemental_measure(payload: SupplementPayload) -> dict[str, Any]:
    """为总分接近的表补录一次质量检查，重新进入贡献视图复测清单。"""
    data = payload.model_dump(exclude_none=True)
    return _service.add_supplemental_measure(
        data.pop("table_code"), data.pop("window"), **data)


@router.post("/measures/{measure_id}/retest")
def retest_measure(measure_id: int, payload: RetestPayload) -> dict[str, Any]:
    """质量规则复测：复测结论回写巡检待办，原始分项依据保留。"""
    return _service.retest_measure(
        measure_id, payload.new_value, payload.conclusion, payload.reviewer)
