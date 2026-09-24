"""请求/响应数据模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class RecognizedChar(BaseModel):
    """单个识别字符及其起止时间（毫秒）。"""

    model_config = ConfigDict(extra="forbid")

    char: str = Field(..., description="识别字符，必须且只能包含 1 个字符")
    start: StrictInt = Field(..., description="起始时间（毫秒）")
    end: StrictInt = Field(..., description="结束时间（毫秒）")


class AlignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recognized: list[RecognizedChar] = Field(
        ..., description="按时间排序的识别字符序列"
    )
    total_duration: StrictInt = Field(
        ..., alias="total_duration",
        description="录音总时长（毫秒），须为非负整数",
    )
    revised_text: str = Field(..., description="人工修订后的完整文本")


class LocateQuoteRequest(AlignRequest):
    """校时输入与引语定位参数。"""

    quote: str = Field(..., min_length=1, description="待查找的非空引语")
    max_edits: StrictInt = Field(
        ..., ge=0, le=2,
        description="允许的插入、删除、替换最大总次数（0～2）",
    )


class CaptionsRequest(AlignRequest):
    """校时输入与字幕切分参数。"""

    max_chars: StrictInt = Field(
        ..., ge=1, le=80,
        description="每段字幕最大字符数（1～80）",
    )
    max_duration_ms: StrictInt = Field(
        ..., ge=500, le=30000,
        description="每段字幕最大时长（毫秒，500～30000）",
    )


class RevisedCharTimeline(BaseModel):
    char: str = Field(..., description="修订文本中的字符")
    start: int = Field(..., description="起始时间（毫秒）")
    end: int = Field(..., description="结束时间（毫秒）")
    source: str = Field(
        ...,
        description="matched=直接匹配到识别字符；computed=按空档均匀推算的新增字符",
    )
    matched_index: int | None = Field(
        None, description="source=matched 时对应的识别字符下标（从 0 开始）"
    )
    recognized_char: str | None = Field(
        None, description="source=matched 时对应的识别字符（替换纠错时与 char 不同）"
    )


class AlignResponse(BaseModel):
    total_duration: int
    chars: list[RevisedCharTimeline]
    inserted_count: int = Field(..., description="新增（推算）字符数")
    deleted_count: int = Field(..., description="被删除、未输出的识别字符数")
    matched_count: int = Field(..., description="匹配/替换到识别字符的修订字符数")
    backend: str = Field(..., description="对齐内核：native(C) 或 python")


class QuoteHit(BaseModel):
    start_index: int = Field(..., description="命中片段在修订文本中的起始字符下标（从 0 开始，半开区间）")
    end_index: int = Field(..., description="命中片段在修订文本中的结束字符下标（不含该位置）")
    text: str = Field(..., description="修订文本中实际命中的文字")
    edits: int = Field(..., description="实际插入、删除、替换总次数")
    start: int = Field(..., description="起始时间（毫秒），取首字符时间轴的 start")
    end: int = Field(..., description="结束时间（毫秒），取末字符时间轴的 end")


class CaptionSegment(BaseModel):
    start_index: int = Field(..., description="段首字符在修订文本中的下标（从 0 开始）")
    end_index: int = Field(..., description="段末字符下标（半开区间，不含该位置）")
    text: str = Field(..., description="该段字幕文字")
    start: int = Field(..., description="段起始毫秒，取首字符时间轴的 start")
    end: int = Field(..., description="段结束毫秒，取末字符时间轴的 end")


class HealthResponse(BaseModel):
    status: str
    backend: str
