"""端到端 API 与时间轴语义测试（native 与 python 两种内核均会覆盖）。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.alignment import align_chars  # noqa: E402
from app.main import app  # noqa: E402
from app.quote import find_quote_hits  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def rc(items):
    return [{"char": c, "start": a, "end": b} for c, a, b in items]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 对齐内核：重复字符结合上下文选择匹配位置，同分优先较早原位置
# ---------------------------------------------------------------------------
def test_duplicate_chars_prefer_earlier_source():
    # aaa -> aa：保留前两个（较早原位置）
    assert align_chars("aaa", "aa") == [0, 1]
    # aa -> aaa：新增字符落在末尾
    assert align_chars("aa", "aaa") == [0, 1, -1]


def test_substitution_is_anchored():
    # 替换纠错优先锚定到识别字符时间槽，而不是全部走删除+新增
    mapping = align_chars("识别错误文字", "识别正确文字")
    assert mapping[2] == 2  # “正” 替换 “错”，锚定下标 2
    assert mapping[3] == 3  # “确” 替换 “误”


def test_insertion_and_deletion():
    mapping = align_chars("你好吗世界", "你好世界")
    assert mapping == [0, 1, 3, 4]  # “吗” 被删除


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["backend"] in ("native", "python")


# ---------------------------------------------------------------------------
# 基本对齐与新增字符空档均分
# ---------------------------------------------------------------------------
def test_basic_align_with_interpolation(client):
    payload = {
        "recognized": rc([
            ("你", 0, 100), ("好", 200, 300), ("世", 400, 500), ("界", 600, 700),
        ]),
        "total_duration": 1000,
        "revised_text": "你好呀世界",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    chars = body["chars"]
    assert [c["char"] for c in chars] == list("你好呀世界")
    assert [c["source"] for c in chars] == [
        "matched", "matched", "computed", "matched", "matched"]

    ni = chars[2]
    # “呀”位于“好”结束(300)与“世”开始(400)之间
    assert ni["start"] == 300 and ni["end"] == 400
    # matched 锚点时间原样保留
    assert (chars[0]["start"], chars[0]["end"]) == (0, 100)
    assert body["inserted_count"] == 1
    assert body["deleted_count"] == 0
    assert body["matched_count"] == 4


def test_multiple_insertions_evenly_split(client):
    # 锚点 a 与 c 之间没有任何识别字符，x、y 为两个纯新增字符，空档 [100,600]
    payload = {
        "recognized": rc([("a", 0, 100), ("c", 600, 700)]),
        "total_duration": 1000,
        "revised_text": "axyc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    x, y = r.json()["chars"][1], r.json()["chars"][2]
    assert x["source"] == "computed" and y["source"] == "computed"
    # [100,600] 分两段：100..350, 350..600
    assert (x["start"], x["end"]) == (100, 350)
    assert (y["start"], y["end"]) == (350, 600)


def test_substitution_tie_anchors_into_gap(client):
    # a b c -> a x y c：x 与 b 替换代价相同且优先锚定，y 落在 b 与 c 的空档
    payload = {
        "recognized": rc([("a", 0, 100), ("b", 200, 300), ("c", 600, 700)]),
        "total_duration": 1000,
        "revised_text": "axyc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    x, y = r.json()["chars"][1], r.json()["chars"][2]
    assert x["source"] == "matched" and x["recognized_char"] == "b"
    assert (x["start"], x["end"]) == (200, 300)
    assert y["source"] == "computed"
    assert (y["start"], y["end"]) == (300, 600)


def test_leading_and_trailing_insertions_use_recording_bounds(client):
    payload = {
        "recognized": rc([("b", 400, 500)]),
        "total_duration": 1000,
        "revised_text": "abc",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    a, b, c = r.json()["chars"]
    # 前缀“a”在录音起点 0 到 b 开始 400 之间
    assert (a["start"], a["end"]) == (0, 400)
    # 后缀“c”在 b 结束 500 到录音总时长 1000 之间
    assert (c["start"], c["end"]) == (500, 1000)
    assert b["source"] == "matched"


def test_deleted_chars_not_output(client):
    payload = {
        "recognized": rc([("a", 0, 100), ("x", 200, 300), ("b", 400, 500)]),
        "total_duration": 600,
        "revised_text": "ab",
    }
    r = client.post("/align", json=payload)
    body = r.json()
    assert [c["char"] for c in body["chars"]] == ["a", "b"]
    assert body["deleted_count"] == 1


def test_substitution_marked_matched_with_recognized_char(client):
    payload = {
        "recognized": rc([("错", 0, 100)]),
        "total_duration": 200,
        "revised_text": "对",
    }
    r = client.post("/align", json=payload)
    body = r.json()
    ch = body["chars"][0]
    assert ch["char"] == "对"
    assert ch["source"] == "matched"
    assert ch["recognized_char"] == "错"
    assert (ch["start"], ch["end"]) == (0, 100)


def test_all_inserted_spans_whole_recording(client):
    payload = {"recognized": [], "total_duration": 300, "revised_text": "三"}
    r = client.post("/align", json=payload)
    ch = r.json()["chars"][0]
    assert ch["source"] == "computed"
    assert (ch["start"], ch["end"]) == (0, 300)


def test_empty_revised_text(client):
    payload = {
        "recognized": rc([("a", 0, 100)]),
        "total_duration": 200,
        "revised_text": "",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["chars"] == []
    assert body["deleted_count"] == 1


def test_timestamps_ordered_and_in_range(client):
    payload = {
        "recognized": rc([("a", 0, 10), ("b", 20, 30), ("c", 40, 50)]),
        "total_duration": 100,
        "revised_text": "xaxbxcx",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    prev_end = 0
    for c in r.json()["chars"]:
        assert 0 <= c["start"] <= c["end"] <= 100
        assert c["start"] >= prev_end
        prev_end = c["end"]


def test_zero_duration(client):
    payload = {"recognized": rc([("a", 0, 0)]), "total_duration": 0,
               "revised_text": "a"}
    r = client.post("/align", json=payload)
    assert r.status_code == 200
    ch = r.json()["chars"][0]
    assert (ch["start"], ch["end"]) == (0, 0)


def test_duplicate_resolved_by_context_to_earlier_source(client):
    # 识别串里出现两个“对”。修订文本在“错”前补“对”、“错”后保留“对”：
    # 第一个“对”必须结合上下文匹配到更早的识别位置 0，而不是误配位置 2。
    payload = {
        "recognized": rc([
            ("对", 0, 100), ("错", 200, 300), ("对", 400, 500),
        ]),
        "total_duration": 600,
        "revised_text": "对错对",
    }
    r = client.post("/align", json=payload)
    assert r.status_code == 200, r.text
    chars = r.json()["chars"]
    assert [c["matched_index"] for c in chars] == [0, 1, 2]


def test_duplicate_identical_target_keeps_earlier_slots(client):
    # aaa（三个相同识别字） -> aa：删除多余重复字符时，保留最早的两个时间槽
    payload = {
        "recognized": rc([("a", 0, 10), ("a", 20, 30), ("a", 40, 50)]),
        "total_duration": 60,
        "revised_text": "aa",
    }
    r = client.post("/align", json=payload)
    chars = r.json()["chars"]
    assert [c["matched_index"] for c in chars] == [0, 1]
    assert (chars[0]["start"], chars[1]["start"]) == (0, 20)
    assert r.json()["deleted_count"] == 1


# ---------------------------------------------------------------------------
# 输入校验：时间无效时给出明确错误
# ---------------------------------------------------------------------------
def _post(client, payload):
    return client.post("/align", json=payload)


def test_negative_total_duration(client):
    r = _post(client, {"recognized": [], "total_duration": -1,
                       "revised_text": "a"})
    assert r.status_code == 422
    assert "非负" in r.json()["detail"]


def test_end_before_start(client):
    r = _post(client, {
        "recognized": rc([("a", 300, 100)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_out_of_recording_range(client):
    r = _post(client, {
        "recognized": rc([("a", 100, 600)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "超出录音总时长" in r.json()["detail"]


def test_not_sorted(client):
    r = _post(client, {
        "recognized": rc([("b", 300, 400), ("a", 0, 100)]),
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "排序" in r.json()["detail"]


def test_overlapping_intervals(client):
    r = _post(client, {
        "recognized": rc([("a", 0, 200), ("b", 100, 300)]),
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "重叠" in r.json()["detail"]


def test_negative_timestamp(client):
    r = _post(client, {
        "recognized": rc([("a", -5, 100)]),
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "负数" in r.json()["detail"]


def test_missing_field(client):
    r = client.post("/align", json={"recognized": [], "total_duration": 100})
    assert r.status_code == 422
    assert "revised_text" in r.json()["detail"]


def test_wrong_type_timestamp(client):
    r = client.post("/align", json={
        "recognized": [{"char": "a", "start": "0", "end": 100}],
        "total_duration": 500, "revised_text": "a"})
    assert r.status_code == 422
    assert "整数" in r.json()["detail"]


def test_multi_char_recognized_item(client):
    r = _post(client, {
        "recognized": [{"char": "ab", "start": 0, "end": 100}],
        "total_duration": 500, "revised_text": "ab"})
    assert r.status_code == 422
    assert "1 个字符" in r.json()["detail"]


def test_float_duration_rejected(client):
    r = _post(client, {"recognized": [], "total_duration": 1.5,
                       "revised_text": "a"})
    assert r.status_code == 422


def test_extra_field_rejected(client):
    r = _post(client, {"recognized": [], "total_duration": 5,
                       "revised_text": "a", "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_zero_duration_multiple_inserts(client):
    r = _post(client, {"recognized": [], "total_duration": 0,
                       "revised_text": "甲乙"})
    assert r.status_code == 200
    chars = r.json()["chars"]
    assert [(c["start"], c["end"]) for c in chars] == [(0, 0), (0, 0)]


# ---------------------------------------------------------------------------
# 引语近似定位
# ---------------------------------------------------------------------------
def test_quote_finder_exact_repeats():
    assert find_quote_hits("abc", "xabcxabc", 1) == [
        (1, 4, 0), (5, 8, 0)]


def test_quote_finder_uses_insertion():
    assert find_quote_hits("你好世界", "你好呀世界", 1) == [(0, 5, 1)]


def test_quote_finder_overlapping_choice_prefers_more_hits():
    # 长度 2 的引语与任一单字符距离均为 1，因此四个互不重叠单字符优先于两个较长命中。
    assert find_quote_hits("ab", "aabb", 1) == [
        (0, 1, 1), (1, 2, 1), (2, 3, 1), (3, 4, 1)]


def test_quote_finder_tie_uses_lower_edit_cost():
    # 同一文本内较短片段可消耗 1 次删除命中；零编辑完整片段优先。
    assert find_quote_hits("abc", "abc", 1) == [(0, 3, 0)]


def test_locate_quote_returns_timeline_ranges(client):
    payload = {
        "recognized": rc([
            ("你", 0, 100), ("好", 200, 300), ("世", 500, 600),
            ("界", 700, 800),
        ]),
        "total_duration": 1000,
        "revised_text": "你好呀世界",
        "quote": "你好世界",
        "max_edits": 1,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "start_index": 0,
        "end_index": 5,
        "text": "你好呀世界",
        "edits": 1,
        "start": 0,
        "end": 800,
    }]


def test_locate_quote_empty_array_when_no_hit(client):
    payload = {
        "recognized": rc([("a", 0, 100)]),
        "total_duration": 200,
        "revised_text": "a",
        "quote": "zzz",
        "max_edits": 2,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 200
    assert r.json() == []


def test_locate_quote_reuses_alignment_validation(client):
    payload = {
        "recognized": rc([("a", 300, 100)]),
        "total_duration": 500,
        "revised_text": "a",
        "quote": "a",
        "max_edits": 0,
    }
    r = client.post("/locate-quote", json=payload)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_locate_quote_empty_quote_rejected(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "", "max_edits": 0})
    assert r.status_code == 422
    assert "非空" in r.json()["detail"]


def test_locate_quote_max_edits_range(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "a", "max_edits": 3})
    assert r.status_code == 422
    assert "0～2" in r.json()["detail"]


def test_locate_quote_extra_field_rejected(client):
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "quote": "a", "max_edits": 0, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


def test_locate_quote_too_long_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_QUOTE_LENGTH", 2)
    r = client.post("/locate-quote", json={
        "recognized": [], "total_duration": 0, "revised_text": "abc",
        "quote": "abc", "max_edits": 0})
    assert r.status_code == 422
    assert "引语长度" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 字幕切分
# ---------------------------------------------------------------------------
def _captions(client, items, revised, max_chars=80, max_duration_ms=30000,
              total=None):
    payload = {
        "recognized": rc(items),
        "total_duration": total if total is not None else 40000,
        "revised_text": revised,
        "max_chars": max_chars,
        "max_duration_ms": max_duration_ms,
    }
    return client.post("/captions", json=payload)


def test_captions_empty_text_returns_empty_array(client):
    r = _captions(client, [], "", total=0)
    assert r.status_code == 200
    assert r.json() == []


def test_captions_prefer_break_after_sentence_punctuation(client):
    # 两种两段切分：[你好][。世界] 与 [你好。][世界]；断点左字为“。”加 2000 分
    r = _captions(client, [
        ("你", 0, 100), ("好", 200, 300), ("。", 400, 500),
        ("世", 600, 700), ("界", 800, 900),
    ], "你好。世界", max_chars=3, total=1000)
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"start_index": 0, "end_index": 3, "text": "你好。",
         "start": 0, "end": 500},
        {"start_index": 3, "end_index": 5, "text": "世界",
         "start": 600, "end": 900},
    ]


def test_captions_punctuation_bonus_applies_to_all_listed_marks(client):
    for mark in "。！？；":
        r = _captions(client, [
            ("甲", 0, 100), (mark, 200, 300), ("乙", 400, 500),
        ], f"甲{mark}乙", max_chars=2, total=600)
        assert r.status_code == 200, r.text
        segs = r.json()
        assert [s["text"] for s in segs] == [f"甲{mark}", "乙"]


def test_captions_maximize_pause_score_without_punctuation(client):
    # 候选断点停顿分别为 100 / 300 / 100 毫秒，选停顿最大者
    r = _captions(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 600, 700), ("d", 800, 900),
    ], "abcd", max_chars=3, total=1000)
    assert [s["text"] for s in r.json()] == ["ab", "cd"]


def test_captions_pause_clamped_then_lexicographic(client):
    # 断点 1 与断点 3 的停顿分别为 1900、3200 毫秒，均截到 1000 而同分，
    # 取断点下标序列字典序最小者
    r = _captions(client, [
        ("a", 0, 100), ("b", 2000, 2100), ("c", 2200, 2300),
        ("d", 5500, 5600),
    ], "abcd", max_chars=3, total=6000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["a", "bcd"]
    assert (segs[0]["start_index"], segs[0]["end_index"]) == (0, 1)
    assert (segs[1]["start_index"], segs[1]["end_index"]) == (1, 4)


def test_captions_fewest_segments_beats_breakpoint_score(client):
    # 一段总分 0 也优于两段（断点在“。”后可得 2000+ 分）
    r = _captions(client, [
        ("a", 0, 100), ("。", 200, 300), ("b", 400, 500),
    ], "a。b", max_chars=3, total=600)
    assert r.json() == [
        {"start_index": 0, "end_index": 3, "text": "a。b",
         "start": 0, "end": 500},
    ]


def test_captions_respect_max_chars(client):
    # 停顿全部相同，同分时取字典序最小断点序列：前段尽量短
    r = _captions(client, [
        ("a", 0, 100), ("b", 200, 300), ("c", 400, 500),
        ("d", 600, 700), ("e", 800, 900),
    ], "abcde", max_chars=2, total=1000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["a", "bc", "de"]
    assert all(s["end_index"] - s["start_index"] <= 2 for s in segs)


def test_captions_respect_max_duration(client):
    # 每段跨度不得超过 1000 毫秒：abc 相连 1400 毫秒超限，只能 ab / cd
    r = _captions(client, [
        ("a", 0, 400), ("b", 500, 900), ("c", 1000, 1400),
        ("d", 1500, 1900),
    ], "abcd", max_duration_ms=1000, total=2000)
    segs = r.json()
    assert [s["text"] for s in segs] == ["ab", "cd"]
    assert (segs[0]["start"], segs[0]["end"]) == (0, 900)
    assert (segs[1]["start"], segs[1]["end"]) == (1000, 1900)


def test_captions_single_char_span_exceeds_limit(client):
    r = _captions(client, [("啊", 0, 600)], "啊",
                  max_duration_ms=500, total=1000)
    assert r.status_code == 422
    assert "超过单段时长上限" in r.json()["detail"]


def test_captions_uses_computed_timeline_for_inserted_chars(client):
    # 新增字符“呀”在空档 [300,400] 内推算，切分以其推算时间为准
    r = _captions(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 400, 500),
    ], "你好呀世", max_chars=2, total=600)
    segs = r.json()
    assert [s["text"] for s in segs] == ["你好", "呀世"]
    assert (segs[1]["start"], segs[1]["end"]) == (300, 500)


def test_captions_reuses_alignment_validation(client):
    r = _captions(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_captions_max_chars_range(client):
    for bad in (0, 81):
        r = _captions(client, [], "", max_chars=bad, total=0)
        assert r.status_code == 422
        assert "1～80" in r.json()["detail"]


def test_captions_max_duration_range(client):
    for bad in (499, 30001):
        r = _captions(client, [], "", max_duration_ms=bad, total=0)
        assert r.status_code == 422
        assert "500～30000" in r.json()["detail"]


def test_captions_extra_field_rejected(client):
    r = client.post("/captions", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "max_chars": 10, "max_duration_ms": 1000, "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 修订比对（revision-diff）
# ---------------------------------------------------------------------------
def _diff(client, items, revised, total=1000):
    payload = {
        "recognized": rc(items),
        "total_duration": total,
        "revised_text": revised,
    }
    return client.post("/revision-diff", json=payload)


def test_revision_diff_no_change_returns_empty_array(client):
    r = _diff(client, [("你", 0, 100), ("好", 200, 300)], "你好", total=400)
    assert r.status_code == 200
    assert r.json() == []


def test_revision_diff_insert_block(client):
    # 新增“呀”落在“好”结束(300)与“世”开始(400)的空档内
    r = _diff(client, [
        ("你", 0, 100), ("好", 200, 300), ("世", 400, 500), ("界", 600, 700),
    ], "你好呀世界")
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "insert",
        "recognized_start_index": 2,
        "recognized_end_index": 2,
        "revised_start_index": 2,
        "revised_end_index": 3,
        "recognized_text": "",
        "revised_text": "呀",
        "start": 300,
        "end": 400,
    }]


def test_revision_diff_delete_block(client):
    r = _diff(client, [
        ("你", 0, 100), ("好", 200, 300), ("吗", 400, 500),
        ("世", 600, 700), ("界", 800, 900),
    ], "你好世界")
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "delete",
        "recognized_start_index": 2,
        "recognized_end_index": 3,
        "revised_start_index": 2,
        "revised_end_index": 2,
        "recognized_text": "吗",
        "revised_text": "",
        "start": 400,
        "end": 500,
    }]


def test_revision_diff_replace_block(client):
    r = _diff(client, [("识", 0, 100), ("错", 200, 300)], "识对", total=400)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "replace",
        "recognized_start_index": 1,
        "recognized_end_index": 2,
        "revised_start_index": 1,
        "revised_end_index": 2,
        "recognized_text": "错",
        "revised_text": "对",
        "start": 200,
        "end": 300,
    }]


def test_revision_diff_merges_adjacent_changes_into_one_block(client):
    # 替换 b→x 与新增 y 之间没有未改动字符，合并为一块；时间取最早起点、最晚终点
    r = _diff(client, [("a", 0, 100), ("b", 200, 300)], "axy")
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "replace",
        "recognized_start_index": 1,
        "recognized_end_index": 2,
        "revised_start_index": 1,
        "revised_end_index": 3,
        "recognized_text": "b",
        "revised_text": "xy",
        "start": 200,
        "end": 1000,
    }]


def test_revision_diff_unchanged_chars_split_blocks(client):
    # 未改动的“界”把前后两处改动隔开；块按原文顺序返回
    r = _diff(client, [
        ("错", 0, 100), ("界", 200, 300), ("吗", 400, 500),
    ], "对界", total=600)
    assert r.status_code == 200, r.text
    blocks = r.json()
    assert [b["type"] for b in blocks] == ["replace", "delete"]
    assert (blocks[0]["recognized_text"], blocks[0]["revised_text"]) == ("错", "对")
    assert (blocks[1]["recognized_text"], blocks[1]["revised_text"]) == ("吗", "")
    assert (blocks[0]["start"], blocks[0]["end"]) == (0, 100)
    assert (blocks[1]["start"], blocks[1]["end"]) == (400, 500)


def test_revision_diff_all_deleted(client):
    r = _diff(client, [("a", 0, 100), ("b", 200, 300)], "", total=400)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "delete",
        "recognized_start_index": 0,
        "recognized_end_index": 2,
        "revised_start_index": 0,
        "revised_end_index": 0,
        "recognized_text": "ab",
        "revised_text": "",
        "start": 0,
        "end": 300,
    }]


def test_revision_diff_all_inserted(client):
    # 无识别字符时，整块新增时间横跨录音范围 [0, total_duration]
    r = _diff(client, [], "甲乙", total=300)
    assert r.status_code == 200, r.text
    assert r.json() == [{
        "type": "insert",
        "recognized_start_index": 0,
        "recognized_end_index": 0,
        "revised_start_index": 0,
        "revised_end_index": 2,
        "recognized_text": "",
        "revised_text": "甲乙",
        "start": 0,
        "end": 300,
    }]


def test_revision_diff_leading_and_trailing_changes(client):
    # 首部插入用录音起点 0 到右锚点的 start，尾部删除保留识别字符自身时间
    r = _diff(client, [
        ("b", 400, 500), ("c", 600, 700), ("d", 800, 900),
    ], "abc", total=1000)
    assert r.status_code == 200, r.text
    blocks = r.json()
    assert [b["type"] for b in blocks] == ["insert", "delete"]
    assert (blocks[0]["revised_text"], blocks[0]["start"], blocks[0]["end"]) == (
        "a", 0, 400)
    assert (blocks[1]["recognized_text"], blocks[1]["start"],
            blocks[1]["end"]) == ("d", 800, 900)


def test_revision_diff_reuses_alignment_validation(client):
    r = _diff(client, [("a", 300, 100)], "a", total=500)
    assert r.status_code == 422
    assert "结束时间" in r.json()["detail"]


def test_revision_diff_extra_field_rejected(client):
    r = client.post("/revision-diff", json={
        "recognized": [], "total_duration": 0, "revised_text": "",
        "bogus": 1})
    assert r.status_code == 422
    assert "不允许" in r.json()["detail"]
