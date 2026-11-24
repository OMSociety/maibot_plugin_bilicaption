"""subtitle_utils 核心逻辑测试（网络环节以桩替代）。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subtitle_utils as su
from subtitle_utils import (
    BvidParseError,
    SubtitleCache,
    SubtitleFetchError,
    SubtitleResult,
    _clean_subtitle_text,
    _is_safe_b23_url,
    parse_video_ref,
    render_view,
    window_text,
)


class TestCleanSubtitleText:
    def test_br_to_newline(self):
        assert _clean_subtitle_text("第一行<br>第二行") == "第一行\n第二行"

    def test_strip_tags_and_entities(self):
        assert _clean_subtitle_text('<font color="#fff">a &amp; b</font>') == "a & b"

    def test_drop_blank_lines(self):
        assert _clean_subtitle_text("a\n\n  \nb") == "a\nb"

    def test_empty(self):
        assert _clean_subtitle_text("") == ""


class TestParseVideoRef:
    @pytest.mark.asyncio
    async def test_bvid(self):
        ref = await parse_video_ref("BV1GJ411x7h7")
        assert (ref.bvid, ref.page) == ("BV1GJ411x7h7", None)

    @pytest.mark.asyncio
    async def test_url_with_page(self):
        ref = await parse_video_ref("https://www.bilibili.com/video/BV1GJ411x7h7/?p=3")
        assert (ref.bvid, ref.page) == ("BV1GJ411x7h7", 3)

    @pytest.mark.asyncio
    async def test_av_forms(self):
        assert (await parse_video_ref("av170001")).bvid == "BV17x411w7KC"
        assert (await parse_video_ref("170001")).bvid == "BV17x411w7KC"
        url = "https://www.bilibili.com/video/av170001"
        assert (await parse_video_ref(url)).bvid == "BV17x411w7KC"

    @pytest.mark.asyncio
    async def test_share_text(self):
        raw = "【标题】 https://www.bilibili.com/video/BV1GJ411x7h7/?share=1 看看"
        assert (await parse_video_ref(raw)).bvid == "BV1GJ411x7h7"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ["", "   ", "这不是链接！！"])
    async def test_invalid(self, raw):
        with pytest.raises(BvidParseError):
            await parse_video_ref(raw)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw", ["https://b23.tv/abc123", "bili2233.cn/xyz789", "https://b23.wtf/q1w2e3"]
    )
    async def test_short_links_dispatch(self, monkeypatch, raw):
        seen = []

        async def fake(url):
            seen.append(url)
            return su.VideoRef("BV1GJ411x7h7")

        monkeypatch.setattr(su, "resolve_short_link", fake)
        assert (await parse_video_ref(raw)).bvid == "BV1GJ411x7h7"
        assert seen and seen[0].startswith("https://")

    @pytest.mark.asyncio
    async def test_bare_short_code(self, monkeypatch):
        seen = []

        async def fake(url):
            seen.append(url)
            return su.VideoRef("BV1GJ411x7h7")

        monkeypatch.setattr(su, "resolve_short_link", fake)
        await parse_video_ref("4bdIZBf")
        assert seen == ["https://b23.tv/4bdIZBf"]


class TestSsrf:
    @pytest.mark.parametrize(
        "url",
        [
            "https://b23.tv/x",
            "http://bili2233.cn/x",
            "https://www.bilibili.com/video/BV1",
        ],
    )
    def test_allowed(self, url):
        assert _is_safe_b23_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x",
            "http://169.254.169.254/latest",
            "https://evil.com/?b23.tv",
            "https://bilibili.com.evil.com/",
            "ftp://b23.tv/x",
            "file:///etc/passwd",
            "",
            "b23.tv/x",
        ],
    )
    def test_rejected(self, url):
        assert not _is_safe_b23_url(url)

    @pytest.mark.asyncio
    async def test_resolve_rejects_foreign_domain(self):
        with pytest.raises(BvidParseError, match="短链"):
            await su.resolve_short_link("https://evil.com/x")


class TestCache:
    def _cache(self, ttl=10, n=2):
        now = [0.0]
        return SubtitleCache(ttl, n, clock=lambda: now[0]), now

    def test_hit_and_miss(self):
        c, _ = self._cache()
        v = SubtitleResult("t", "x")
        c.put("a", v)
        assert c.get("a") is v
        assert c.get("b") is None

    def test_expiry_purges(self):
        c, now = self._cache()
        c.put("a", SubtitleResult("t", "x"))
        now[0] = 11
        assert c.get("a") is None
        assert len(c) == 0

    def test_put_purges_expired(self):
        c, now = self._cache(n=5)
        c.put("a", SubtitleResult("t", "x"))
        now[0] = 11
        c.put("b", SubtitleResult("t", "y"))
        assert len(c) == 1

    def test_lru_eviction_not_full_clear(self):
        c, _ = self._cache(n=2)
        for k in "abc":
            c.put(k, SubtitleResult("t", k))
        assert c.get("a") is None
        assert c.get("b") and c.get("c")

    def test_get_refreshes_recency(self):
        c, _ = self._cache(n=2)
        c.put("a", SubtitleResult("t", "a"))
        c.put("b", SubtitleResult("t", "b"))
        c.get("a")
        c.put("c", SubtitleResult("t", "c"))
        assert c.get("a") and c.get("b") is None

    def test_disabled(self):
        c, _ = self._cache(ttl=0)
        c.put("a", SubtitleResult("t", "x"))
        assert c.get("a") is None and len(c) == 0

    def test_configure_shrinks(self):
        c, _ = self._cache(n=5)
        for k in "abc":
            c.put(k, SubtitleResult("t", k))
        c.configure(10, 1)
        assert len(c) == 1 and c.get("c")
        c.configure(0, 1)
        assert len(c) == 0


class TestWindow:
    def test_within_limit(self):
        w = window_text("a" * 10, 20)
        assert w.is_complete and w.head == "a" * 10

    def test_unlimited(self):
        assert window_text("a" * 100, 0).is_complete

    def test_head_tail(self):
        text = "".join(str(i % 10) for i in range(1000))
        w = window_text(text, 100)
        assert len(w.head) == 60 and len(w.tail) == 40
        assert w.head == text[:60] and w.tail == text[-40:]
        assert w.omitted_from == 60 and w.omitted_to == 960
        assert w.next_offset == 60

    def test_offset_paging(self):
        text = "x" * 250
        w = window_text(text, 100, offset=100)
        assert (w.start, w.end, w.next_offset) == (100, 200, 200)
        w = window_text(text, 100, offset=200)
        assert (w.end, w.next_offset) == (250, None)

    @pytest.mark.parametrize("off", [-1, 250, 999])
    def test_offset_out_of_range(self, off):
        with pytest.raises(SubtitleFetchError, match="offset"):
            window_text("x" * 250, 100, offset=off)


class TestRenderView:
    def test_complete(self):
        out = render_view("T", "hello", 100)
        assert "T" in out and "已完整展示" in out and out.endswith("hello")

    def test_truncated_reports_meta(self):
        out = render_view("T", "x" * 1000, 100)
        assert "全文 1000 字符" in out and "offset=60" in out and "省略" in out

    def test_offset_tail_note(self):
        out = render_view("T", "x" * 150, 100, offset=100)
        assert "已到全文末尾" in out

    def test_offset_next(self):
        out = render_view("T", "x" * 500, 100, offset=0)
        assert "offset=100" in out


class TestFetchErrors:
    @pytest.mark.asyncio
    async def test_requires_login(self):
        with pytest.raises(SubtitleFetchError, match="登录"):
            await su.fetch_subtitle("BV1GJ411x7h7", None)

    @pytest.mark.asyncio
    async def test_bad_page(self):
        with pytest.raises(SubtitleFetchError, match="分 P"):
            await su.fetch_subtitle("BV1GJ411x7h7", object(), page=0)

    @pytest.mark.parametrize(
        ("code", "needle"),
        [
            (-404, "不存在"),
            (62002, "不存在"),
            (-412, "风控"),
            (-101, "登录"),
            (12345, "12345"),
        ],
    )
    def test_api_error_mapping(self, code, needle):
        e = su.ResponseCodeException(code, "m", {})
        assert needle in str(su._api_error(e, "BVx"))


class TestLoadSubtitle:
    @pytest.mark.asyncio
    async def test_cache_shared_across_pages_and_forms(self, monkeypatch):
        calls = []

        async def fake_fetch(bvid, credential, page=1):
            calls.append((bvid, page))
            return SubtitleResult("t", "text")

        monkeypatch.setattr(su, "fetch_subtitle", fake_fetch)
        cache = SubtitleCache(60, 8)
        cred = object()
        await su.load_subtitle("av170001", None, cred, cache)
        await su.load_subtitle("BV17x411w7KC", None, cred, cache)
        await su.load_subtitle(
            "https://www.bilibili.com/video/BV17x411w7KC/?p=2", None, cred, cache
        )
        await su.load_subtitle("BV17x411w7KC", 2, cred, cache)
        assert calls == [("BV17x411w7KC", 1), ("BV17x411w7KC", 2)]
