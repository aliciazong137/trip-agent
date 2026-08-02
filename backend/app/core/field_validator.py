"""
确定性字段校验（第一期，review 第3点）

设计原则：
  - 不依赖 LLM 自评 confidence（LLM 自评不稳定）
  - 用确定性规则判断"是否可以进入规划"
  - confidence 只作日志指标，不参与决策

判断逻辑：
  can_plan = (not missing_required) and (not invalid)

  missing_required：必填字段缺失（city, days）
  invalid：字段有值但值不合法（如 days=100）
"""
from typing import Tuple, List, Optional

from app.models.schemas import TripMeta


# 必填字段（缺这些就进 needs_clarification）
REQUIRED_FIELDS = ["city", "days"]

# 可选字段（有则校验合法性，缺失不算 missing）
OPTIONAL_FIELDS = ["travelers", "budget", "pace", "preferences", "must_visit", "avoid"]


def validate_trip_meta(raw: dict) -> Tuple[Optional[TripMeta], List[str], List[str]]:
    """
    校验 IntentRecognizer 抽取的 trip_meta

    Args:
        raw: IntentRecognizer 输出的 trip_meta dict（可能含部分字段或为 None）

    Returns:
        (trip_meta, missing_fields, invalid_fields)
        - trip_meta: 校验通过则返回 TripMeta 实例，否则 None
        - missing_fields: 缺失的必填字段名
        - invalid_fields: 有值但不合法的字段名
    """
    missing: List[str] = []
    invalid: List[str] = []

    if not raw or not isinstance(raw, dict):
        return None, list(REQUIRED_FIELDS), []

    # 1. city 校验
    city = raw.get("city")
    if not city or not isinstance(city, str) or not city.strip():
        missing.append("city")
    else:
        # 标准化：去首尾空格
        raw["city"] = city.strip()

    # 2. days 校验
    days = raw.get("days")
    if days is None:
        missing.append("days")
    elif isinstance(days, bool):
        # bool 是 int 子类，单独拦截
        invalid.append("days")
    elif not isinstance(days, int):
        # 字符串数字也接受（LLM 偶尔返回 "2"）
        try:
            days = int(days)
            raw["days"] = days
        except (TypeError, ValueError):
            invalid.append("days")
            days = None
    if days is not None and not (1 <= days <= 30):
        invalid.append("days")

    # 3. travelers 可选，有则校验结构
    travelers = raw.get("travelers")
    if travelers is not None:
        if not isinstance(travelers, dict):
            invalid.append("travelers")
        else:
            adults = travelers.get("adults", 0)
            children = travelers.get("children", 0)
            # kids 和 children 同义（schemas 用 kids，但 prompt 用 children）
            if "kids" in travelers and "children" not in travelers:
                children = travelers.get("kids", 0)
                travelers["children"] = children
            try:
                a, c = int(adults), int(children)
                if a < 0 or c < 0:
                    invalid.append("travelers")
                else:
                    travelers["adults"] = a
                    travelers["children"] = c
                    # 同步给 schemas 用的 kids 字段
                    travelers["kids"] = c
            except (TypeError, ValueError):
                invalid.append("travelers")

    # 4. budget 可选，有则校验
    budget = raw.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            invalid.append("budget")
        else:
            amount = budget.get("amount")
            if amount is not None:
                try:
                    budget["amount"] = float(amount)
                    if budget["amount"] < 0:
                        invalid.append("budget")
                except (TypeError, ValueError):
                    invalid.append("budget")

    # 5. pace 可选，有则校验枚举
    pace = raw.get("pace")
    if pace is not None and pace not in ("relaxed", "normal", "packed"):
        invalid.append("pace")

    # 6. must_visit / avoid 可选，有则校验类型
    for field in ("must_visit", "avoid"):
        val = raw.get(field)
        if val is not None and not isinstance(val, list):
            invalid.append(field)

    # 7. preferences / transportation / accommodation / start_date 可选，
    # LLM 偶尔会把 str 字段返回成 [] 或其他类型，统一规范化
    for str_field in ("preferences", "transportation", "accommodation", "start_date"):
        val = raw.get(str_field)
        if val is None:
            continue
        if isinstance(val, list):
            raw[str_field] = ", ".join(str(v) for v in val) if val else None
        elif isinstance(val, str):
            if not val.strip():
                raw[str_field] = None
        elif isinstance(val, (int, float)):
            raw[str_field] = str(val)
        else:
            invalid.append(str_field)

    # 7. 构建 TripMeta（若必填齐全且无非法）
    if not missing and not invalid:
        try:
            trip_meta = TripMeta.model_validate(raw)
            return trip_meta, missing, invalid
        except Exception as e:
            # Pydantic 兜底校验失败
            logger.warning("TripMeta model_validate failed: %s", e)
            return None, missing, ["_schema_validation"]

    return None, missing, invalid


def can_plan(missing: List[str], invalid: List[str]) -> bool:
    """是否可以进入规划阶段：必填齐全且无非法值"""
    return not missing and not invalid


def build_clarification_question(missing: List[str], invalid: List[str]) -> str:
    """
    根据缺失/非法字段生成人类可读的澄清问题

    设计：
      - 不同字段给不同的提示文案
      - 同时缺 city 和 days 时合并问，不分两次反问
    """
    parts = []

    # city
    if "city" in missing:
        parts.append("您想去哪个城市？（如：北京、南京、上海）")
    elif "city" in invalid:
        parts.append("城市名称似乎不完整，请明确具体城市。")

    # days
    if "days" in missing:
        parts.append("计划玩几天？（如：2 天、3 天）")
    elif "days" in invalid:
        parts.append("天数需要是 1-30 的整数，请确认您计划的天数。")

    # travelers
    if "travelers" in invalid:
        parts.append("出行人数格式有误，请说明成人和儿童各几位（如：两大一小）。")

    # budget
    if "budget" in invalid:
        parts.append("预算信息有误，请说明总预算或人均预算金额。")

    # pace
    if "pace" in invalid:
        parts.append("节奏请选 relaxed/normal/packed 之一。")

    # schema 兜底
    if "_schema_validation" in invalid:
        parts.append("部分字段格式不符合规范，请补充核心信息（城市、天数、人数、预算）。")

    if not parts:
        return "请补充更多行程信息。"

    return "为了帮您生成准确的行程，请补充以下信息：\n" + "\n".join(f"- {p}" for p in parts)


# 模块级 logger（避免循环导入）
import logging
logger = logging.getLogger(__name__)
