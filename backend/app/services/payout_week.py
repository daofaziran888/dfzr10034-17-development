"""结算表周表（payout_week）：评分对象之一。

特征：质量证据分布与反馈表相反——质量测量充分（含检查得零分的记录），
但元数据稀疏（已登记元数据少、分级/血缘/契约等元数据字段缺失）。
"""
from __future__ import annotations

from app.services.base import BaseModuleService


class PayoutWeekService(BaseModuleService):
    MODULE = "payout_week"
    ENTITY = "结算表周表"
    OPERATION_TERM = "登记"
    LIST_FIELDS = [
        "表编号", "周次", "负责人", "业务域", "字段总数", "已登记元数据",
        "分级标签数", "血缘关系数", "契约状态", "负责人到位", "标签覆盖",
        "质量测量条数", "元数据完备性", "备注",
    ]
    REQUIRED_FIELDS = ["表编号", "周次"]
    STATUS_ORDER = ["待评分", "已评分", "复测中"]
    ACTION_RULES = {"发起评分": "已评分", "发起复测": "复测中"}
    NEGATIVE_ACTIONS = []
