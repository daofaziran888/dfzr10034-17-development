"""评分贡献视图演示场景：把题目叙事固化成可重复构建的数据。

场景结构：
- 2026-W45：两张历史周表。weekly_active_week 总分高且证据干净；retention_week
  总分低但含实际零分检查。历史排名（按总分）与本周排查次序（按待复测/零分）冲突。
  retention_week 评分签发后补登记一次质量测量，重算时报告证据漂移但原始评分不变。
- 2026-W46：反馈表 survey_week（质量维度弱：检查无证据；元数据齐）与结算表
  payout_week（质量证据充分且含实际零分；元数据稀疏），总分接近但证据分布相反。
- 两张接近总分的表各补录一次质量检查，进入贡献视图的下一轮复测清单；
  对实际零分检查执行复测，复测结论回写巡检待办 schedule_job。

幂等：通过 pipeline state 标记，重复调用不会重复造数；store.reset() 后可重建。
"""
from __future__ import annotations

from app.services.govern_score import GovernScoreService
from app.store import store

W45 = "2026-W45"
W46 = "2026-W46"
DEMO_FLAG = "demo:score_contribution"


def build_demo_scenario() -> dict[str, object]:
    state = store.get_pipeline_state(DEMO_FLAG)
    if state and state.get("built"):
        return {"ok": True, "already_built": True}

    svc = GovernScoreService()

    # ── W45 历史窗口：排名与排查次序冲突 + 签发后补登记 ──────────
    svc.qms.create_measure(
        "weekly_active_week", W45, "完整性", 95, 100, 1.0, 99.0,
        check_code="QMS-WA-01")
    svc.issue_with_evidence("weekly_active_week", W45, {
        "元数据完备性": 88, "分级标签准确度": 88, "血缘完备性": 88,
        "标签覆盖": 88, "契约合规": 88, "负责人到位": 88,
    })

    svc.qms.create_measure(
        "retention_week", W45, "完整性", 95, 100, 1.0, 70.0,
        check_code="QMS-RT-01")
    svc.issue_with_evidence("retention_week", W45, {
        "元数据完备性": 88, "分级标签准确度": 88, "血缘完备性": 88,
        "标签覆盖": 88, "契约合规": 88, "负责人到位": 88,
    })
    # 评分签发后补登记一次质量测量：窗口守卫检查通过（同窗口新检查编号），
    # 已签发评分的原始依据保持不变，只在重算漂移与复测清单中出现。
    svc.register_late_measurement(
        "retention_week", W45, check_type="准确性",
        threshold_low=80, threshold_high=100, weight=0.0,
        measured_value=91.0, check_code="QMS-RT-02-LATE")

    # ── W46：反馈表（无证据）与结算表（实际零分），总分接近 ─────
    # 反馈表：唯一质量检查未采集到证据 → 质量维度无证据、不计权重，与零分区分。
    svc.qms.create_measure(
        "survey_week", W46, "完整性", 95, 100, 0.4, "",
        check_code="QMS-SUR-01", note="反馈表质量维度弱：本周未采集")
    survey = svc.issue_with_evidence("survey_week", W46, {
        "元数据完备性": 75, "分级标签准确度": 70, "血缘完备性": 72,
        "标签覆盖": 68, "契约合规": 70, "负责人到位": 65,
    })

    # 结算表：三条检查证据齐全，其中完整性实测 82 低于阈值 95，实际零分。
    svc.qms.create_measure(
        "payout_week", W46, "完整性", 95, 100, 0.1, 82.0,
        check_code="QMS-PAY-01")
    svc.qms.create_measure(
        "payout_week", W46, "准确性", 80, 100, 0.4, 93.0,
        check_code="QMS-PAY-02")
    svc.qms.create_measure(
        "payout_week", W46, "一致性", 80, 100, 0.3, 90.0,
        check_code="QMS-PAY-03")
    payout = svc.issue_with_evidence("payout_week", W46, {
        "元数据完备性": 60, "分级标签准确度": 65, "血缘完备性": 62,
        "标签覆盖": 75, "契约合规": 90, "负责人到位": 90,
    })

    # ── 两张接近总分的表各补录一次质量检查（重新进入贡献视图）──
    svc.add_supplemental_measure(
        "survey_week", W46, check_type="完整性",
        threshold_low=95, threshold_high=100, weight=0.6,
        measured_value=96.0, check_code="QMS-SUR-02")
    svc.add_supplemental_measure(
        "payout_week", W46, check_type="一致性",
        threshold_low=80, threshold_high=100, weight=0.2,
        measured_value=88.0, check_code="QMS-PAY-04")

    # ── 对结算表实际零分检查复测，结论回写巡检待办 ─────────────
    zero_measure = svc.qms.find_by_check_code("QMS-PAY-01")
    svc.retest_measure(zero_measure["id"], 97.0, "整改后复测通过，完整性恢复至阈值内", "质量评审组")

    store.set_pipeline_state(DEMO_FLAG, {"built": True})
    return {
        "ok": True,
        "already_built": False,
        "windows": [W45, W46],
        "survey_overall": survey["overall"],
        "payout_overall": payout["overall"],
    }
