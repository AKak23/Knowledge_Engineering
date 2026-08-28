#!/usr/bin/env python3
"""Deterministic English→Chinese translation for ontology attribute & action names.

Object/relation names already carry Chinese descriptions in the source DSL, so they
are translated directly from those descriptions in the converter. Attribute names
(`customer_id`) and action names (`schedule_campaign`) are English identifiers and
are translated here by composing per-token Chinese plus a few whole-name overrides.

`attr_zh()` / `action_zh()` never leave English behind: any token missing from the
maps is reported by `residual_ascii()` so the maps can be completed.
"""
from __future__ import annotations

import re

# ----------------------------------------------------------------------------
# Noun / modifier / unit tokens (shared by attributes and action object-nouns).
# ----------------------------------------------------------------------------
TOKEN: dict[str, str] = {
    # units & measures
    "id": "ID", "no": "号", "at": "时间", "date": "日期", "time": "时间",
    "km": "(公里)", "kg": "(公斤)", "mm": "(毫米)", "mb": "(MB)", "gb": "(GB)",
    "mbps": "(Mbps)", "gbps": "(Gbps)", "mhz": "(MHz)", "ppm": "(ppm)",
    "min": "最小", "max": "最大", "minute": "(分钟)", "minutes": "(分钟)",
    "hour": "(小时)", "hours": "(小时)", "days": "(天)", "months": "(月)",
    "years": "年限", "pct": "比例", "per": "每", "of": "", "to": "至",
    "is": "是否", "from": "起始", "until": "截止", "on": "在", "hand": "在手",
    # general nouns
    "account": "账户", "accuracy": "准确率", "action": "操作", "activity": "活动",
    "activated": "激活", "actual": "实际", "address": "地址", "agent": "坐席",
    "agreement": "协议", "alarm": "告警", "algorithm": "算法", "amount": "金额",
    "annual": "年度", "approval": "审批", "approved": "已批准", "approver": "审批人",
    "area": "领域", "article": "知识文章", "assessment": "测评", "asset": "资产",
    "attempt": "尝试", "attribution": "归因", "audit": "审计", "availability": "可用率",
    "axis": "轴", "axle": "车轴", "azimuth": "方位角", "badge": "工牌",
    "balance": "余额", "band": "频段", "bandwidth": "带宽", "base": "基本",
    "batch": "批次", "benefit": "福利", "bill": "账单", "billing": "账单",
    "bin": "库位", "bonus": "奖金", "branch": "分支", "bu": "业务单元",
    "budget": "预算", "bundle": "套餐", "buyer": "采购员", "campaign": "活动",
    "candidate": "候选人", "capacity": "容量", "captured": "采集", "carrier": "承运商",
    "case": "案例", "catalog": "目录", "category": "类别", "cause": "原因",
    "cc": "成本中心", "cert": "证书", "certification": "认证", "change": "变更",
    "channel": "渠道", "check": "检查", "city": "城市", "class": "类别",
    "coaching": "辅导", "coding": "编程", "code": "编码", "comment": "备注",
    "community": "社群", "company": "公司", "competency": "胜任力", "competition": "竞赛",
    "component": "组件", "concentration": "浓度", "condition": "条件", "config": "配置",
    "constraint": "约束", "contact": "联系人", "content": "内容", "contract": "合同",
    "control": "管理", "conversion": "转化", "count": "数量", "country": "国家",
    "course": "课程", "coverage": "覆盖", "cr": "变更请求", "created": "创建",
    "creative": "创意素材", "credit": "信用", "criticality": "关键度", "csat": "客户满意度",
    "culture": "文化", "currency": "币种", "customer": "客户", "cycle": "周期",
    "data": "数据", "deal": "交易", "defect": "缺陷", "delivery": "交付",
    "department": "部门", "departure": "出发", "description": "描述", "detail": "明细",
    "discount": "折扣", "distance": "距离", "doc": "文档", "dormant": "休眠",
    "driver": "司机", "due": "截止", "duration": "时长", "education": "学历",
    "employee": "员工", "end": "结束", "engagement": "互动", "engineer": "工程师",
    "enrollment": "报名", "estimate": "预估", "estimated": "预估", "expected": "预期",
    "experience": "经验", "experiment": "实验", "expertise": "专长", "fault": "故障",
    "fee": "费用", "feedback": "反馈", "fit": "匹配度", "forecast": "预测",
    "frequency": "频率", "function": "职能", "gender": "性别", "gift": "礼品",
    "goal": "目标", "goods": "货物", "gr": "收货", "grade": "等级",
    "handling": "处理", "hc": "编制", "headcount": "编制", "hire": "入职",
    "horizon": "预测周期", "hub": "枢纽", "iccid": "ICCID", "identity": "身份",
    "incident": "事件", "increase": "涨幅", "industry": "行业", "inspection": "检验",
    "inspector": "检验员", "intensity": "强度", "interaction": "互动", "interval": "间隔",
    "interview": "面试", "interviewer": "面试官", "inventory": "库存", "invoice": "发票",
    "issued": "签发", "item": "条目", "kind": "类型", "kr": "关键结果", "label": "标签",
    "lat": "纬度", "lead": "线索", "learner": "学员", "leave": "假期",
    "level": "级别", "license": "许可", "lifecycle": "生命周期", "lift": "提升度",
    "limit": "上限", "line": "行", "link": "链路", "list": "标准",
    "location": "位置", "log": "日志", "logged": "记录", "lon": "经度",
    "lost": "流失", "lot": "批号", "loyalty": "会员", "machine": "机床",
    "maintenance": "维护", "manager": "经理", "mape": "MAPE", "market": "市场",
    "material": "物料", "mentorship": "导师辅导", "merit": "绩效调薪", "method": "方法",
    "metric": "指标", "model": "型号", "module": "模块", "monthly": "月度",
    "movement": "库存移动", "name": "名称", "nc": "不合格", "ne": "网元",
    "negotiation": "谈判", "neighbor": "邻区", "nps": "NPS", "number": "编号",
    "objective": "目标", "occurred": "发生", "offer": "权益", "onboarding": "入职",
    "opened": "开启", "operation": "工序", "operator": "操作员", "opportunity": "商机",
    "order": "订单", "org": "组织", "outcome": "结果", "overdue": "逾期",
    "package": "套餐", "paid": "支付", "parameter": "参数", "part": "零件",
    "partnership": "合作", "party": "往来方", "path": "路径", "payment": "支付",
    "payroll": "薪资发放", "period": "期间", "person": "人员", "phone": "电话",
    "pip": "绩效改进", "plan": "计划", "planned": "计划", "planner": "计划员",
    "po": "采购订单", "points": "积分", "position": "岗位", "posting": "招聘启事",
    "predicted": "预测", "preference": "偏好", "price": "价格", "primary": "主",
    "priority": "优先级", "probability": "概率", "process": "工艺", "produced": "生产",
    "product": "产品", "production": "生产", "profile": "档案", "program": "培养项目",
    "promotion": "促销", "proposal": "方案", "province": "省份", "publish": "发布",
    "purchase": "采购", "qty": "数量", "quality": "质量", "quantity": "数量",
    "quota": "配额", "quotation": "报价", "quote": "报价", "quoted": "报价",
    "raised": "提出", "rate": "费率", "rater": "评价人", "rating": "评分",
    "reading": "读数", "receipt": "收货", "received": "已收", "record": "记录",
    "recommendation": "建议", "recruiter": "招聘官", "referral": "推荐", "refrigerated": "冷藏",
    "region": "区域", "relation": "关系", "rep": "销售代表", "report": "报告",
    "req": "需求", "request": "申请", "requester": "申请人", "requisition": "招聘需求",
    "reservation": "预订", "resolution": "解决", "resource": "资源", "response": "响应",
    "restructuring": "重组", "result": "结果", "resume": "简历", "review": "评估",
    "reward": "奖励", "rfm": "RFM", "risk": "风险", "role": "角色",
    "round": "轮次", "route": "路线", "routing": "工艺路线", "run": "运行",
    "salary": "薪资", "sample": "样本", "scheduled": "排期", "scheme": "方案",
    "score": "评分", "screening": "初筛", "segment": "分群", "sentiment": "情绪",
    "sequence": "序", "service": "服务", "session": "会话", "severity": "严重程度",
    "shift": "班次", "shipment": "发运", "signal": "信号", "signed": "签署",
    "site": "站点", "size": "规模", "skill": "技能", "sku": "SKU",
    "sla": "SLA", "slot": "工位", "source": "来源", "span": "管理幅度",
    "spec": "规格", "specialty": "专业", "spent": "已花费", "stage": "阶段",
    "standard": "标准", "start": "开始", "started": "开始", "station": "工位",
    "status": "状态", "stock": "库存", "style": "风格", "subcase": "子案例",
    "subcontract": "分包", "submit": "提交", "subscription": "订阅", "summary": "摘要",
    "supplier": "供应商", "survey": "调研", "tag": "标签", "target": "目标",
    "tariff": "资费", "task": "任务", "tax": "税额", "team": "团队",
    "tech": "技术员", "term": "期限", "text": "文本", "ticket": "工单",
    "tie": "关系", "tier": "层级", "title": "职称", "tolerance": "公差",
    "tool": "刀具", "topic": "主题", "total": "合计", "touchpoint": "触点",
    "track": "方向", "trigger": "触发", "trip": "行程", "tuning": "调优",
    "type": "类型", "unit": "单位", "uom": "计量单位", "uploaded": "上传",
    "utilization": "利用率", "valid": "有效", "value": "值", "variant": "实验组",
    "vehicle": "车辆", "vendor": "供应商", "verified": "已验证", "version": "版本",
    "vesting": "归属", "vip": "VIP", "voice": "语音", "vpn": "VPN",
    "warehouse": "仓库", "wear": "磨损", "weight": "权重", "wo": "工单",
    "workorder": "工单", "workshop": "车间", "zone": "区域", "background": "背景",
}

# ----------------------------------------------------------------------------
# Action leading verbs.
# ----------------------------------------------------------------------------
VERB: dict[str, str] = {
    "abandon": "放弃", "abolish": "撤销", "accept": "接受", "achieve": "达成",
    "acknowledge": "受理", "activate": "激活", "advance": "推进", "allocate": "分配",
    "analyze": "分析", "apply": "执行", "approve": "审批", "archive": "归档",
    "assign": "分派", "block": "冻结", "breach": "违反", "calibrate": "校准",
    "cancel": "取消", "churn": "流失", "claim": "领取", "clear": "清除",
    "click": "点击", "close": "关闭", "commit": "确认", "complete": "完成",
    "compute": "计算", "conclude": "结束", "confirm": "确认", "consume": "消耗",
    "convert": "转化", "create": "创建", "deactivate": "停用", "decline": "拒绝",
    "degrade": "降级", "deliver": "交付", "depart": "发车", "deprecate": "弃用",
    "dispute": "提出争议", "disqualify": "淘汰", "draft": "起草", "drop": "退出",
    "empty": "清空", "end": "结束", "enroll": "报名", "escalate": "升级",
    "execute": "执行", "expire": "过期", "fail": "判定失败", "finalize": "终结",
    "finish": "完成", "form": "建立", "freeze": "冻结", "fulfill": "履行",
    "grade": "评分", "hold": "暂扣", "implement": "实施", "ingest": "接入",
    "interrupt": "中断", "issue": "签发", "launch": "启动", "leave": "离职",
    "load": "装车", "lose": "输单", "make": "发起", "mark": "标记",
    "match": "匹配", "merge": "合并", "occupy": "占用", "onboard": "入职",
    "open": "开启", "park": "停放", "pass": "通过", "pause": "暂停",
    "pay": "支付", "pickup": "揽收", "plan": "排程", "post": "过账",
    "process": "处理", "propose": "提出", "provision": "开通", "publish": "发布",
    "qualify": "确认资格", "reactivate": "重新激活", "ready": "置为就绪", "receive": "接收",
    "reconcile": "对账", "refund": "退款", "register": "注册", "reject": "拒绝",
    "release": "下达", "replace": "更换", "report": "上报", "reserve": "预留",
    "resolve": "解决", "respond": "响应", "restore": "恢复", "resume": "恢复",
    "retire": "下线", "return": "退回", "revert": "回退", "revoke": "吊销",
    "reward": "发放奖励", "rollback": "回滚", "schedule": "排期", "screen": "筛选",
    "send": "发送", "set": "设置", "settle": "结算", "share": "分享",
    "ship": "发货", "sign": "签署", "skip": "跳过", "snapshot": "快照",
    "start": "开始", "submit": "提交", "supersede": "取代", "suspend": "暂停",
    "terminate": "终止", "transfer": "调动", "transport": "运输", "tune": "调优",
    "unblock": "解封", "use": "使用", "verify": "核验", "void": "作废",
    "waive": "豁免", "win": "赢单",
}

# Whole-name overrides where token order or idiom needs fixing.
ATTR_OVERRIDE: dict[str, str] = {
    "name": "名称", "status": "状态", "type": "类型", "value": "值",
    "amount": "金额", "qty": "数量", "quantity": "数量", "category": "类别",
    "on_hand": "在手库存", "years_experience": "工作年限", "span_of_control": "管理幅度",
    "rate_per_mb": "每MB费率", "rate_per_minute": "每分钟费率", "rate_per_min": "每分钟费率",
    "is_primary": "是否主联系人", "is_refrigerated": "是否冷藏", "sequence_no": "序号",
    "line_total": "行金额", "list_price": "标准价格", "unit_price": "单价",
    "total_amount": "总金额", "tax_amount": "税额", "line_id": "行号",
    "valid_from": "生效起始", "valid_until": "有效截止", "valid_to": "有效截止",
    "size_estimate": "规模预估", "horizon_days": "预测周期(天)", "vesting_years": "归属年限",
    "term_months": "期限(月)", "annual_leave_days": "年假天数", "response_minutes": "响应时长(分钟)",
    "duration_minutes": "时长(分钟)", "duration_min": "时长(分钟)", "service_hours": "服务时长(小时)",
    "target_resolution_minutes": "目标解决时长(分钟)", "max_concentration_ppm": "最大浓度(ppm)",
    "data_quota_gb": "流量配额(GB)", "voice_minutes": "语音时长(分钟)",
    "capacity_per_hour": "每小时产能", "capacity_kg": "载重(公斤)", "capacity_gbps": "容量(Gbps)",
    "bandwidth_mbps": "带宽(Mbps)", "distance_km": "距离(公里)", "weight_kg": "重量(公斤)",
    "tolerance_mm": "公差(毫米)", "frequency_mhz": "频率(MHz)", "axle_count": "轴数",
    "axis_count": "轴数", "station_count": "基站数", "headcount_quota": "编制名额",
    "culture_fit_score": "文化匹配评分", "coding_score": "编程评分", "rfm_score": "RFM评分",
    "credit_level": "信用等级", "credit_limit": "信用额度", "approval_limit": "审批额度",
    "service_type": "服务类型", "license_class": "准驾车型", "license_model": "牌照类型",
    "license_model_id": "牌照类型", "skill_level": "技能等级", "vip_level": "VIP等级",
    "on_hand_id": "在手库存", "actual_count": "实际数量", "actual_value": "实际值",
    "expected_amount": "预期金额", "estimated_amount": "预估金额", "forecast_amount": "预测金额",
    "annual_value": "年度金额", "monthly_fee": "月费", "base_salary": "基本薪资",
    "min_salary": "最低薪资", "max_salary": "最高薪资", "discount_pct": "折扣比例",
    "discount_rate": "折扣率", "lift_pct": "提升比例", "received_qty": "已收数量",
    "predicted_qty": "预测数量", "stock_qty": "库存数量", "production_capacity": "生产产能",
    "availability_target": "可用率目标", "target_value": "目标值", "approved_count": "审批数量",
    "bundle_size": "套餐规格", "points_balance": "积分余额", "service_id": "服务ID",
    "relation_kind": "关系类型", "relation_type": "关系类型", "tie_type": "关系类型",
    "asset_type": "资产类型", "account_type": "账户类型", "customer_type": "客户类型",
    "channel_type": "渠道类型", "component_type": "组件类型", "ne_type": "网元类型",
    "doc_type": "文档类型", "alarm_type": "告警类型", "change_type": "变更类型",
    "shift_type": "班次类型", "resource_type": "资源类型", "hub_type": "枢纽类型",
    "zone_type": "区域类型", "warehouse_type": "仓库类型", "function_type": "职能类型",
    "vpn_type": "VPN类型", "branch_label": "分支标签", "material_code": "物料编码",
    "sku_code": "SKU编码", "description_text": "描述文本", "task_name": "任务名称",
    "gift_name": "礼品名称", "close_date": "成交日期", "delivery_date": "交付日期",
    "due_date": "截止日期", "hire_date": "入职日期", "invoice_date": "开票日期",
    "issue_date": "签发日期", "order_date": "下单日期", "po_date": "采购日期",
    "publish_date": "发布日期", "received_date": "收货日期", "scheduled_date": "排期日期",
    "signed_date": "签署日期", "start_date": "开始日期", "end_date": "结束日期",
    "submit_date": "提交日期", "start_time": "开始时间", "end_time": "结束时间",
    "planned_start": "计划开始", "planned_end": "计划结束", "interval_days": "间隔天数",
    "response_time": "响应时间", "credit": "信用", "quality": "质量",
}

ACTION_OVERRIDE: dict[str, str] = {
    "advance_opportunity_to_proposal": "推进商机至方案",
    "advance_opportunity_to_negotiation": "推进商机至谈判",
    "advance_to_interview": "推进至面试",
    "post_goods_receipt": "收货过账",
    "release_order_to_production": "下达订单至生产",
    "send_to_maintenance": "送修",
    "set_machine_maintenance": "置机床为维护态",
    "set_ne_maintenance": "置网元为维护态",
    "complete_background_check": "完成背景调查",
    "verify_identity": "核验身份",
    "mark_dormant": "标记为休眠",
    "mark_lost": "标记为丢单",
    "mark_overdue": "标记为逾期",
    "convert_lead": "转化线索",
    "convert_touchpoint": "转化触点",
    "accept_offer": "接受录用", "decline_offer": "拒绝录用", "issue_offer": "发放权益",
    "complete_payroll": "完成薪资发放", "process_payroll": "处理薪资发放", "fail_payroll": "薪资发放失败",
    "open_pip": "启动绩效改进", "conclude_pip": "结束绩效改进",
    "start_restructuring": "启动组织重组", "implement_org_change": "实施组织变更",
    "approve_org_change": "审批组织变更", "submit_change_request": "提交变更请求",
    "release_review": "下达评估", "calibrate_review": "校准评估", "calibrate_cycle": "校准周期",
    "make_referral": "发起推荐", "reward_referral": "奖励推荐", "accept_referral": "接受推荐",
    "reject_referral": "拒绝推荐", "claim_reward": "领取奖励", "void_reward": "作废奖励",
    "issue_reward": "发放奖励", "reward": "发放奖励",
    "snapshot_inventory": "库存快照", "reconcile_count": "盘点对账", "start_count": "开始盘点",
    "post_movement": "过账库存移动", "cancel_movement": "取消库存移动",
    "open_billing_cycle": "开启账期", "close_billing_cycle": "关闭账期",
    "finalize_billing_cycle": "终结账期", "breach_sla": "违反SLA",
    "tune_ne": "调优网元", "block_ne": "冻结网元", "unblock_ne": "解封网元",
    "restore_ne": "恢复网元", "ingest_signal": "接入信号", "apply_config": "应用配置",
    "propose_config": "提出配置", "revert_config": "回退配置",
    "ready_task": "任务置为就绪", "rollback_task": "回滚任务", "block_task": "冻结任务",
    "respond_survey": "回复调研", "share_feedback": "分享反馈", "submit_feedback": "提交反馈",
    "win_opportunity": "赢得商机", "lose_opportunity": "输掉商机",
    "use_tool": "使用刀具", "return_tool": "归还刀具",
    "return_from_leave": "休假返岗", "leave_employee": "员工离职",
    "compute_accuracy": "计算准确率", "skip_operation": "跳过工序", "finish_operation": "完成工序",
    "abolish_position": "撤销岗位", "freeze_position": "冻结岗位",
    "merge_company": "合并公司", "form_partnership": "建立合作", "end_partnership": "结束合作",
    "deprecate_routing": "弃用工艺路线", "release_routing": "下达工艺路线",
    "set": "设置",
}

_ATTR_SUFFIX = {"id": "ID", "at": "时间"}


def _translate_tokens(tokens: list[str]) -> str:
    return "".join(TOKEN.get(t, f"⟦{t}⟧") for t in tokens)


def attr_zh(name: str) -> str:
    if name in ATTR_OVERRIDE:
        return ATTR_OVERRIDE[name]
    tokens = name.split("_")
    suffix = ""
    if tokens and tokens[-1] in _ATTR_SUFFIX and len(tokens) > 1:
        suffix = _ATTR_SUFFIX[tokens[-1]]
        tokens = tokens[:-1]
    return _translate_tokens(tokens) + suffix


def action_zh(name: str) -> str:
    if name in ACTION_OVERRIDE:
        return ACTION_OVERRIDE[name]
    tokens = name.split("_")
    verb = VERB.get(tokens[0], f"⟦{tokens[0]}⟧")
    noun = _translate_tokens(tokens[1:]) if len(tokens) > 1 else ""
    return verb + noun


_ASCII = re.compile(r"[A-Za-z]")
_KEEP_ASCII = ("ID", "MB", "GB", "Mbps", "Gbps", "MHz", "ppm", "SKU", "SLA",
               "VIP", "VPN", "RFM", "NPS", "ICCID", "MAPE", "KR")


def residual_ascii(text: str) -> bool:
    """True if `text` still contains an untranslated English token (⟦...⟧ or stray latin)."""
    if "⟦" in text:
        return True
    stripped = text
    for kw in sorted(_KEEP_ASCII, key=len, reverse=True):
        stripped = stripped.replace(kw, "")
    return bool(_ASCII.search(stripped))
