"""治理评分贡献视图端到端测试。

覆盖需求：
1. 贡献视图按质量检查记录和权重计算每项贡献，列出影响总分的原始检查；
2. 评分签发后补登记质量测量：原始评分不变，重算报告证据漂移；
3. 历史排名与本周排查次序冲突；
4. 按原口径重算兼容已有评分；无证据与实际零分分开；
5. 同一检查不能同时为两个窗口贡献权重（窗口守卫）；
6. 复测结论回写巡检待办，最初评分分项依据保留；
7. survey_week（质量弱、元数据齐）与 payout_week（证据相反）总分接近，
   能指明前者被哪项检查拖累、后者是缺采集证据还是检查零分；
8. 两张接近总分的表各补录一次质量检查，重新进入贡献视图并列出复测项目。
"""
import pytest

import app.store as store_mod
from app.demo import W45, W46, build_demo_scenario
from app.services.govern_score import GovernScoreService
from app.services.quality_measure import QualityMeasureService
from app.services.schedule_job import ScheduleJobService


@pytest.fixture()
def svc():
    store_mod.store.reset()
    build_demo_scenario()
    return GovernScoreService()


def _view(svc, table, window=W46):
    return svc.get_contribution_view(table_code=table, window=window)


def _quality_dim(view):
    return next(d for d in view["breakdown"]["dimensions"] if d["dimension"] == "质量监控覆盖")


# ── 1. 贡献视图与权重计算 ──────────────────────────────────────

class TestContributionView:

    def test_view_lists_raw_checks_and_weighted_contributions(self, svc):
        view = _view(svc, "payout_week")
        quality = _quality_dim(view)
        codes = {c["check_code"] for c in quality["contributors"]}
        assert codes == {"QMS-PAY-01", "QMS-PAY-02", "QMS-PAY-03"}
        # 每个贡献都带权重、检查分、证据状态
        for c in quality["contributors"]:
            assert "weight" in c and "check_score" in c and "evidence_state" in c
        # 原始检查明细完整暴露给评审
        assert view["quality_evidence_summary"]["raw_check_count"] == 3
        # 总分与快照一致
        assert view["score"]["overall"] == pytest.approx(72.7, abs=0.05)

    def test_each_score_carries_its_own_evidence_snapshot(self, svc):
        survey = _view(svc, "survey_week")
        payout = _view(svc, "payout_week")
        survey_codes = {c["check_code"] for d in survey["breakdown"]["dimensions"]
                        for c in d["contributors"]}
        payout_codes = {c["check_code"] for d in payout["breakdown"]["dimensions"]
                        for c in d["contributors"]}
        assert survey_codes == {"QMS-SUR-01"}
        assert "QMS-PAY-01" in payout_codes
        # 评审能指明每个分数用了哪份证据（签发时间固化）
        assert survey["score"]["issued_at"] and payout["score"]["issued_at"]


# ── 2/3. 补登记：原评分不变 + 重算漂移 + 排名冲突 ──────────────

class TestLateRegistration:

    def test_late_measure_does_not_change_issued_score(self, svc):
        row = svc._find_score("retention_week", W45)
        before = row["当前分数"]
        snapshot_codes_before = {
            c["check_code"] for d in row["score_breakdown"]["dimensions"]
            for c in d["contributors"]
        }
        result = svc.register_late_measurement(
            "retention_week", W45, check_type="及时性",
            threshold_low=80, threshold_high=100, weight=0.0,
            measured_value=88.0, check_code="QMS-RT-03-LATE")
        assert result["ok"] is True
        assert row["当前分数"] == before
        snapshot_codes_after = {
            c["check_code"] for d in row["score_breakdown"]["dimensions"]
            for c in d["contributors"]
        }
        assert snapshot_codes_after == snapshot_codes_before
        assert "QMS-RT-03-LATE" not in snapshot_codes_after

    def test_recompute_reports_late_evidence_drift_but_stays_compatible(self, svc):
        row = svc._find_score("retention_week", W45)
        result = svc.recompute_score(row["id"])
        assert result["ok"] is True
        assert result["compatible"] is True
        drift_codes = {d["check_code"] for d in result["late_registered_evidence"]}
        assert "QMS-RT-02-LATE" in drift_codes
        assert result["basis"] == "evidence_snapshot"

    def test_historical_rank_vs_inspection_order_conflict(self, svc):
        view_active = _view(svc, "weekly_active_week", W45)
        view_retention = _view(svc, "retention_week", W45)
        # 历史排名：active 总分高排第 1
        assert view_active["ranking"]["historical_rank"] == 1
        assert view_retention["ranking"]["historical_rank"] == 2
        # 本周排查次序：retention 有零分检查 + 补登记待复测，排在 active 前面
        assert view_retention["ranking"]["inspection_order"] == 1
        assert view_active["ranking"]["inspection_order"] == 2
        assert view_retention["ranking"]["order_conflict"] is True
        assert view_active["ranking"]["order_conflict"] is True


# ── 4. 无证据 vs 实际零分 ──────────────────────────────────────

class TestEvidenceStates:

    def test_survey_quality_dimension_is_no_evidence_not_zero(self, svc):
        view = _view(svc, "survey_week")
        quality = _quality_dim(view)
        # 无证据：质量维度分为 None（不贡献权重），而不是 0
        assert quality["score"] is None
        no_evidence = view["quality_evidence_summary"]["no_evidence_checks"]
        assert [c["check_code"] for c in no_evidence] == ["QMS-SUR-01"]
        assert view["quality_evidence_summary"]["actual_zero_checks"] == []
        # 无证据检查的 check_score 为 None，不会被当成零分
        assert quality["contributors"][0]["check_score"] is None

    def test_payout_quality_dimension_has_actual_zero_check(self, svc):
        view = _view(svc, "payout_week")
        quality = _quality_dim(view)
        zero = {c["check_code"]: c for c in quality["contributors"]
                if c["evidence_state"] == "实际零分"}
        assert "QMS-PAY-01" in zero
        # 实际零分：检查执行了、实测 82 低于阈值 95，检查得 0 分并拉低维度分
        assert zero["QMS-PAY-01"]["check_score"] == 0
        assert quality["score"] is not None and quality["score"] < 90
        assert view["quality_evidence_summary"]["no_evidence_checks"] == []

    def test_evidence_state_classifier(self):
        ev = QualityMeasureService.evidence_state
        assert ev("", 95, 100) == "无证据"
        assert ev(None, 95, 100) == "无证据"
        assert ev(82.0, 95, 100) == "实际零分"
        assert ev(98.0, 95, 100) == "有证据"
        assert QualityMeasureService.check_score("", 95, 100) is None
        assert QualityMeasureService.check_score(82.0, 95, 100) == 0.0


# ── 5. 窗口守卫：同一检查不能同时为两个窗口贡献权重 ────────────

class TestWindowGuard:

    def test_same_check_code_rejected_in_second_window(self, svc):
        with pytest.raises(ValueError):
            svc.qms.create_measure(
                "payout_week", "2026-W47", "完整性", 95, 100, 0.5, 96.0,
                check_code="QMS-PAY-02")

    def test_weight_sum_guard_within_window(self, svc):
        with pytest.raises(ValueError):
            svc.qms.create_measure(
                "survey_week", W46, "及时性", 80, 100, 0.9, 90.0,
                check_code="QMS-SUR-WEIGHT")

    def test_same_check_can_stay_in_its_own_window(self, svc):
        # 同窗口同编号幂等保护由 find_by_check_code 兜底：换编号同窗口可登记
        measure = svc.qms.create_measure(
            "survey_week", W46, "及时性", 80, 100, 0.0, 90.0,
            check_code="QMS-SUR-OK")
        assert measure["检查编号"] == "QMS-SUR-OK"


# ── 6. 复测回写巡检待办 + 原始依据保留 ─────────────────────────

class TestRetest:

    def test_retest_writes_back_to_schedule_todo(self, svc):
        measure = svc.qms.find_by_check_code("QMS-PAY-01")
        result = svc.retest_measure(measure["id"], 97.0, "整改后通过", "评审组")
        assert result["ok"] is True
        todo = result["inspection_todo"]
        assert todo["治理类型"] == "质量巡检待办"
        assert todo["关联检查编号"] == "QMS-PAY-01"
        assert todo["复测结论"] == "整改后通过"
        assert todo["status"] == "已完成"
        # schedule_job 表里可查到回写记录
        jobs = [j for j in store_mod.store.rows("schedule_job")
                if j.get("关联检查编号") == "QMS-PAY-01"]
        assert jobs and jobs[0]["巡检状态"] == "复测已回写"

    def test_original_breakdown_preserved_after_retest(self, svc):
        row = svc._find_score("payout_week", W46)
        original_overall = row["当前分数"]
        original_zero = [
            c["check_code"] for d in row["score_breakdown"]["dimensions"]
            for c in d["contributors"] if c["evidence_state"] == "实际零分"]
        measure = svc.qms.find_by_check_code("QMS-PAY-01")
        svc.retest_measure(measure["id"], 97.0, "整改后通过", "评审组")
        # 最初评分的分项依据不变（快照里 QMS-PAY-01 仍是实际零分）
        assert row["当前分数"] == original_overall
        assert "QMS-PAY-01" in original_zero
        refreshed = _view(svc, "payout_week")
        zero_now = [c["check_code"] for c in refreshed["quality_evidence_summary"]["actual_zero_checks"]]
        assert "QMS-PAY-01" in zero_now
        # 复测结果挂在测量记录与待办上
        assert measure["复测状态"] == "已复测"
        assert measure["复测结论"] == "整改后通过"


# ── 7. 两张接近总分的表：拖累检查与证据归因 ────────────────────

class TestCloseScores:

    def test_two_tables_have_close_totals(self, svc):
        survey = _view(svc, "survey_week")["score"]["overall"]
        payout = _view(svc, "payout_week")["score"]["overall"]
        assert abs(survey - payout) <= 3.0

    def test_reviewer_can_name_check_dragging_survey(self, svc):
        # 前一张（survey_week）被哪项检查拖累：QMS-SUR-01 无证据（未采集）
        view = _view(svc, "survey_week")
        dragged = view["quality_evidence_summary"]["no_evidence_checks"]
        assert [c["check_code"] for c in dragged] == ["QMS-SUR-01"]
        assert view["quality_evidence_summary"]["evidence"] == "无证据"

    def test_reviewer_can_distinguish_missing_evidence_from_zero(self, svc):
        survey = _view(svc, "survey_week")
        payout = _view(svc, "payout_week")
        # survey：缺采集证据（无证据，不是零分）
        assert survey["quality_evidence_summary"]["no_evidence_checks"]
        assert not survey["quality_evidence_summary"]["actual_zero_checks"]
        # payout：证据采集了，是检查得零分
        assert not payout["quality_evidence_summary"]["no_evidence_checks"]
        assert payout["quality_evidence_summary"]["actual_zero_checks"]

    def test_evidence_distribution_is_opposite(self, svc):
        survey_row = store_mod.store.find("survey_week", 1)
        payout_row = store_mod.store.find("payout_week", 1)
        # 反馈表元数据齐、质量弱；结算表元数据稀疏、质量检查多
        assert survey_row["元数据完备性"] == "齐全"
        assert payout_row["元数据完备性"] == "稀疏"
        survey_checks = len(svc.qms.window_measures("survey_week", W46))
        payout_checks = len(svc.qms.window_measures("payout_week", W46))
        assert payout_checks > survey_checks


# ── 8. 补录检查重新进入贡献视图 ────────────────────────────────

class TestSupplementalMeasures:

    def test_supplemental_measures_appear_in_retest_list(self, svc):
        survey_items = {i["check_code"]: i for i in _view(svc, "survey_week")["next_retest_items"]}
        payout_items = {i["check_code"]: i for i in _view(svc, "payout_week")["next_retest_items"]}
        assert "QMS-SUR-02" in survey_items and survey_items["QMS-SUR-02"]["entry_kind"] == "补录检查"
        assert "QMS-PAY-04" in payout_items and payout_items["QMS-PAY-04"]["entry_kind"] == "补录检查"

    def test_supplement_does_not_rewrite_issued_breakdown(self, svc):
        row = svc._find_score("survey_week", W46)
        before = row["当前分数"]
        svc.add_supplemental_measure(
            "survey_week", W46, check_type="及时性",
            threshold_low=80, threshold_high=100, weight=0.0,
            measured_value=90.0, check_code="QMS-SUR-03")
        assert row["当前分数"] == before
        codes = {c["check_code"] for d in row["score_breakdown"]["dimensions"]
                 for c in d["contributors"]}
        assert "QMS-SUR-03" not in codes
        # 但进入下一轮复测清单
        assert "QMS-SUR-03" in {i["check_code"] for i in svc.next_retest_items(row)}

    def test_supplement_end_to_end_through_view_and_retest(self, svc):
        added = svc.add_supplemental_measure(
            "payout_week", W46, check_type="准确性",
            threshold_low=80, threshold_high=100, weight=0.0,
            measured_value=85.0, check_code="QMS-PAY-05")
        view = _view(svc, "payout_week")
        assert added["measure"]["id"] in {i["measure_id"] for i in view["next_retest_items"]}
        result = svc.retest_measure(added["measure"]["id"], 91.0, "补录复测达标", "评审组")
        assert result["ok"] is True
        view2 = _view(svc, "payout_week")
        assert "QMS-PAY-05" not in {i["check_code"] for i in view2["next_retest_items"]}


# ── 9. 旧评分兼容 ──────────────────────────────────────────────

class TestLegacyCompatibility:

    def test_legacy_evaluate_still_works_and_view_marks_basis(self):
        store_mod.store.reset()
        svc = GovernScoreService()
        result = svc.evaluate_asset("legacy-asset")
        view = svc.get_contribution_view(score_id=result["score_id"])
        assert view["score"]["scoring_basis"] == "legacy"
        assert all(not d["contributors"] for d in view["breakdown"]["dimensions"])
        recompute = svc.recompute_score(result["score_id"])
        assert recompute["basis"] == "legacy_rule"
        assert recompute["compatible"] is True

    def test_demo_is_idempotent(self, svc):
        again = build_demo_scenario()
        assert again["already_built"] is True
        # 不会重复生成评分
        scores = store_mod.store.rows("govern_score")
        assert len([s for s in scores if s.get("scoring_basis") == "evidence_v1"]) == 4
