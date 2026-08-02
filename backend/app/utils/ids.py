"""
ID 合法性校验 - 移植自 src/shared/ids.ts

- sessionId: 项目固定格式 sess_ + 12 位 hex（orchestrator 生成）
- resultId: 搜索缓存文件名，1-64 字符的字母数字下划线连字符

严格限制字符集和长度，杜绝路径穿越。
"""
import re
from typing import Union

SESSION_ID_RE = re.compile(r"^sess_[a-f0-9]{12}$")
RESULT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def is_valid_session_id(value: Union[str, object]) -> bool:
    """sessionId 类型守卫"""
    return isinstance(value, str) and SESSION_ID_RE.match(value) is not None


def assert_valid_session_id(value: Union[str, object]) -> str:
    """sessionId 断言，非法时抛 ValueError"""
    if not is_valid_session_id(value):
        raise ValueError(f"invalid sessionId: {value!r}")
    return value  # type: ignore[return-value]


def is_valid_result_id(value: Union[str, object]) -> bool:
    """resultId 类型守卫"""
    return isinstance(value, str) and RESULT_ID_RE.match(value) is not None
