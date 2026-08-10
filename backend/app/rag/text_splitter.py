"""
中文文本切分（第二期 RAG）

自己实现，不引 langchain-text-splitters（减少依赖）。

策略：
  1. 优先按 \n\n 切（段落）
  2. 段落超过 chunk_size 时按 \n 切
  3. 仍超则按 。；，等中文标点切
  4. chunk 之间保留 overlap 字符，保证上下文连续

注意：
  - 不切在中文字符中间（按标点切，保留完整语义）
  - 切完后每个 chunk 加前后 context_chars 上下文，便于检索命中后理解
"""
from dataclasses import dataclass
from typing import List


# 中文常见断句标点（按优先级排序）
_SENTENCE_DELIMITERS = ["。", "！", "？", "！", "?"]
_CLAUSE_DELIMITERS = ["；", ";", "，", ","]


@dataclass
class TextChunk:
    """切分后的文本块"""
    content: str       # 切分后的正文
    context_before: str  # 前置上下文（前一个 chunk 末尾若干字符）
    context_after: str   # 后置上下文（后一个 chunk 开头若干字符）
    chunk_index: int     # 在原文中的序号


def _split_by_delimiters(text: str, delimiters: List[str]) -> List[str]:
    """按指定标点切，标点保留在前一段末尾"""
    if not text:
        return []
    result = []
    buf = []
    for ch in text:
        buf.append(ch)
        if ch in delimiters:
            result.append("".join(buf))
            buf = []
    if buf:
        result.append("".join(buf))
    return result


def _split_paragraph(text: str, max_size: int) -> List[str]:
    """
    把单个段落切成不超过 max_size 的子段
    优先级：\n → 句号 → 分号 → 逗号
    """
    if len(text) <= max_size:
        return [text] if text.strip() else []

    # 试 \n
    if "\n" in text:
        parts = text.split("\n")
        result = []
        for p in parts:
            result.extend(_split_paragraph(p, max_size))
        return result

    # 试句号级
    sentences = _split_by_delimiters(text, _SENTENCE_DELIMITERS)
    if len(sentences) > 1:
        return _merge_pieces(sentences, max_size)

    # 试分句级
    clauses = _split_by_delimiters(text, _CLAUSE_DELIMITERS)
    if len(clauses) > 1:
        return _merge_pieces(clauses, max_size)

    # 实在没法切（无标点长文本），硬切
    return [text[i : i + max_size] for i in range(0, len(text), max_size)]


def _merge_pieces(pieces: List[str], max_size: int) -> List[str]:
    """把小片段合并到不超过 max_size"""
    result = []
    buf = ""
    for p in pieces:
        if not p:
            continue
        if len(buf) + len(p) <= max_size:
            buf += p
        else:
            if buf:
                result.append(buf)
            buf = p
    if buf:
        result.append(buf)
    return result


def split_text(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    context_chars: int = 20,
) -> List[TextChunk]:
    """
    切分长文本为带上下文的 chunks

    Args:
        text: 原文
        chunk_size: 单个 chunk 最大字符数
        overlap: 相邻 chunk 间重叠字符数（让语义连续）
        context_chars: 每个 chunk 记录前后多少字符作为上下文

    Returns:
        List[TextChunk]，每个含 content + context_before + context_after + chunk_index
    """
    if not text or not text.strip():
        return []

    # 1. 按 \n\n 切段落
    paragraphs = [p for p in text.split("\n\n") if p.strip()]

    # 2. 每段切成 chunk_size 大小
    raw_chunks: List[str] = []
    for para in paragraphs:
        raw_chunks.extend(_split_paragraph(para, chunk_size))

    if not raw_chunks:
        return []

    # 3. 加 overlap（把前一个 chunk 的末尾拼到当前 chunk 开头）
    overlapped: List[str] = []
    for i, c in enumerate(raw_chunks):
        if i == 0 or overlap <= 0:
            overlapped.append(c)
        else:
            prev_tail = raw_chunks[i - 1][-overlap:]
            overlapped.append(prev_tail + c)

    # 4. 构造 TextChunk，记录前后上下文
    result: List[TextChunk] = []
    for i, c in enumerate(overlapped):
        ctx_before = raw_chunks[i - 1][-context_chars:] if i > 0 else ""
        ctx_after = raw_chunks[i + 1][:context_chars] if i + 1 < len(raw_chunks) else ""
        result.append(TextChunk(
            content=c.strip(),
            context_before=ctx_before.strip(),
            context_after=ctx_after.strip(),
            chunk_index=i,
        ))

    return result
