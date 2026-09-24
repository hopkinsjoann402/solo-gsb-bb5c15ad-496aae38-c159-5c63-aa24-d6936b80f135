"""修订比对：把逐字时间轴（与 /align 同一对齐结果）整理成有序改动块。

规则：
- 未匹配到的识别字符 → 删除；
- 无来源的修订字符（computed）→ 插入；
- 匹配但来源字符不同 → 替换；
- 以未改动的匹配字符为边界，合并连续改动为一块；
- 块内同时涉及两侧文字（含插删混合）→ 归为 replace；
- 块时间取涉及的原识别字符与修订时间轴字符的最早起点、最晚终点。
"""
from __future__ import annotations

from .schemas import AlignRequest, RevisedCharTimeline, RevisionDiffBlock


def build_revision_diff(req: AlignRequest,
                        chars: list[RevisedCharTimeline]
                        ) -> list[RevisionDiffBlock]:
    """按原文顺序返回改动块；无改动时返回空列表。"""
    recognized = req.recognized
    revised = req.revised_text
    m = len(recognized)

    blocks: list[RevisionDiffBlock] = []
    i = 0  # 已消费的识别字符下标
    # 当前累积块：[识别侧起点, 修订侧起点, 最早 start, 最晚 end]
    block: list[int] | None = None

    def extend(rec_lo: int, rev_lo: int, start: int, end: int) -> None:
        nonlocal block
        if block is None:
            block = [rec_lo, rev_lo, start, end]
        else:
            block[2] = min(block[2], start)
            block[3] = max(block[3], end)

    def close(rec_hi: int, rev_hi: int) -> None:
        nonlocal block
        if block is None:
            return
        rec_lo, rev_lo, start, end = block
        if rec_lo == rec_hi:
            kind = "insert"
        elif rev_lo == rev_hi:
            kind = "delete"
        else:
            kind = "replace"
        blocks.append(RevisionDiffBlock(
            type=kind,
            recognized_start_index=rec_lo,
            recognized_end_index=rec_hi,
            revised_start_index=rev_lo,
            revised_end_index=rev_hi,
            recognized_text="".join(
                item.char for item in recognized[rec_lo:rec_hi]),
            revised_text=revised[rev_lo:rev_hi],
            start=start,
            end=end,
        ))
        block = None

    for j, ch in enumerate(chars):
        if ch.matched_index is None:
            # 插入：无来源的修订字符，取其推算时间。
            extend(i, j, ch.start, ch.end)
            continue
        while i < ch.matched_index:
            # 删除：未匹配到的识别字符。
            item = recognized[i]
            extend(i, j, item.start, item.end)
            i += 1
        if ch.char == ch.recognized_char:
            # 未改动的匹配字符是改动块的边界。
            close(i, j)
        else:
            # 替换：来源字符不同（matched 字符继承识别字符时间，两侧相同）。
            item = recognized[i]
            extend(i, j, item.start, item.end)
        i += 1

    while i < m:
        item = recognized[i]
        extend(i, len(chars), item.start, item.end)
        i += 1
    close(m, len(chars))
    return blocks
