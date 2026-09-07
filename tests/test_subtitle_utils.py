"""
BiliCaption 核心逻辑测试

直接测试 subtitle_utils.py 中的真实函数（不依赖网络与 AstrBot 环境）。
resolve_b23 等网络环节通过 monkeypatch 桩替代。
"""

import asyncio
import os
import sys

import aiohttp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subtitle_utils import (
    BvidParseError,
    SubtitleFetchError,
    _clean_subtitle_text,
    _truncate,
    fetch_subtitle,
    normalize_bvid,
    resolve_b23,
)


class TestCleanSubtitleText:
    """字幕文本清洗测试"""

    def test_br_to_newline(self):
        """<br> 应还原为换行"""
        assert _clean_subtitle_text("第一行<br>第二行") == "第一行\n第二行"

    def test_strip_html_tags(self):
        """font 等标签应去除、保留文字"""
        assert _clean_subtitle_text('<font color="#E5E5E5">内容</font>') == "内容"

    def test_mixed_tags_and_br(self):
        """标签与 <br> 混用"""
        raw = '<font color="#E5E5E5">第一行<br>第二行</font>'
        assert _clean_subtitle_text(raw) == "第一行\n第二行"

    def test_html_entities(self):
        """HTML 实体应还原"""
        assert _clean_subtitle_text("A &amp; B &lt;C&gt;") == "A & B <C>"

    def test_collapse_blank_lines(self):
        """连续换行产生的空行应合并"""
        assert _clean_subtitle_text("a<br><br><br>b") == "a\nb"

    def test_empty_and_none(self):
        """空输入返回空字符串"""
        assert _clean_subtitle_text("") == ""
        assert _clean_subtitle_text(None) == ""


class TestTruncate:
    """字幕截断测试"""

    def test_no_limit(self):
        """max_len <= 0 不截断"""
        text = "A" * 100
        assert _truncate(text, 0) == text

    def test_short_text_unchanged(self):
        """未超限时原样返回"""
        text = "short"
        assert _truncate(text, 100) == text

    def test_exact_length_unchanged(self):
        """恰好等于上限时不截断"""
        text = "A" * 10
        assert _truncate(text, 10) == text

    def test_long_text_truncated(self):
        """超限时截断并带省略提示"""
        result = _truncate("A" * 100, 10)
        assert len(result) < 50
        assert "省略" in result


class TestNormalizeBvid:
    """链接规范化测试（resolve_b23 用桩替代，避免真实网络请求）"""

    def test_pure_bvid(self, monkeypatch):
        """纯 BV 号直接识别，不走短链解析"""
        calls = []

        async def fake_resolve(url: str) -> str:
            calls.append(url)
            raise BvidParseError("不应触发短链解析")

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        assert asyncio.run(normalize_bvid("BV1GJ411x7h7")) == "BV1GJ411x7h7"
        assert calls == []

    def test_full_url(self, monkeypatch):
        """完整 B 站链接应提取出 BV 号（核心修复点）"""

        async def fake_resolve(url: str) -> str:
            raise BvidParseError("不应触发短链解析")

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        raw = "https://www.bilibili.com/video/BV1GJ411x7h7/?spm_id_from=333.999"
        assert asyncio.run(normalize_bvid(raw)) == "BV1GJ411x7h7"

    def test_b23_url(self, monkeypatch):
        """b23 短链交给 resolve_b23，且原始 URL 原样传入"""
        captured = {}

        async def fake_resolve(url: str) -> str:
            captured["url"] = url
            return "BV1GJ411x7h7"

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        assert asyncio.run(normalize_bvid("https://b23.tv/4bdIZBf")) == "BV1GJ411x7h7"
        assert captured["url"] == "https://b23.tv/4bdIZBf"

    def test_bare_short_code(self, monkeypatch):
        """裸短码（如 4bdIZBf）兜底按 b23.tv 解析"""
        captured = {}

        async def fake_resolve(url: str) -> str:
            captured["url"] = url
            return "BV1GJ411x7h7"

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        assert asyncio.run(normalize_bvid("4bdIZBf")) == "BV1GJ411x7h7"
        assert captured["url"] == "https://b23.tv/4bdIZBf"

    def test_empty_input(self, monkeypatch):
        """空输入抛 BvidParseError"""

        async def fake_resolve(url: str) -> str:
            raise BvidParseError("不应触发短链解析")

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        with pytest.raises(BvidParseError):
            asyncio.run(normalize_bvid(""))
        with pytest.raises(BvidParseError):
            asyncio.run(normalize_bvid("   "))

    def test_resolve_failure(self, monkeypatch):
        """短链解析失败时抛 BvidParseError"""

        async def fake_resolve(url: str) -> str:
            raise BvidParseError("短链中未找到 BV 号")

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        with pytest.raises(BvidParseError):
            asyncio.run(normalize_bvid("https://b23.tv/4bdIZBf"))

    def test_bv_with_b23_substring_not_misjudged(self, monkeypatch):
        """BV 号含 b23 子串不应被误判为短链（核心修复点）"""
        calls = []

        async def fake_resolve(url: str) -> str:
            calls.append(url)
            raise BvidParseError("不应触发短链解析")

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        # BV1b2345678x 含 "b23" 但非短链，应被识别为 BV 号
        assert asyncio.run(normalize_bvid("BV1b2345678x")) == "BV1b2345678x"
        assert calls == []

    def test_b23_substring_in_path_not_routed(self, monkeypatch):
        """主机名非 b23 域的 URL 不按短链处理（urlparse 按 hostname 精确判断）"""
        calls = []

        async def fake_resolve(url: str) -> str:
            calls.append(url)
            raise BvidParseError("不应触发短链解析")

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        # 路径含 "b23.tv" 但主机名是 example.com：不当作短链解析，
        # 也过不了裸短码字符集门禁，直接抛 BvidParseError
        with pytest.raises(BvidParseError):
            asyncio.run(normalize_bvid("https://example.com/b23.tv/4bdIZBf"))
        assert calls == []

    def test_bare_code_gate_rejects_non_code_text(self, monkeypatch):
        """裸短码门禁：非 5-13 位字母数字的输入直接拒绝，不发请求"""
        calls = []

        async def fake_resolve(url: str) -> str:
            calls.append(url)
            raise BvidParseError("不应触发短链解析")

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        with pytest.raises(BvidParseError):
            asyncio.run(normalize_bvid("这不是一个链接的普通文本"))
        assert calls == []


class TestResolveB23ErrorHandling:
    """b23 短链解析失败路径测试（网络异常/非法域名均抛 BvidParseError）"""

    def test_timeout_raises(self, monkeypatch):
        """超时（TimeoutError）应抛 BvidParseError 而非崩溃"""

        class FakeResponse:
            def __init__(self):
                self.headers = {}

            async def __aenter__(self):
                raise TimeoutError("simulated timeout")

            async def __aexit__(self, *args):
                return False

        class FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def get(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("subtitle_utils.aiohttp.ClientSession", FakeSession)
        with pytest.raises(BvidParseError):
            asyncio.run(resolve_b23("https://b23.tv/abc"))

    def test_connection_error_raises(self, monkeypatch):
        """连接错误（ClientError）应抛 BvidParseError 而非崩溃"""

        class FakeResponse:
            def __init__(self):
                self.headers = {}

            async def __aenter__(self):
                raise aiohttp.ClientConnectionError("simulated connection error")

            async def __aexit__(self, *args):
                return False

        class FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def get(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("subtitle_utils.aiohttp.ClientSession", FakeSession)
        with pytest.raises(BvidParseError):
            asyncio.run(resolve_b23("https://b23.tv/abc"))

    def test_unsafe_domain_rejected(self, monkeypatch):
        """非 b23 域名的输入直接拒绝，不发请求"""

        class _TripwireSession:
            def __init__(self, *args, **kwargs):
                raise AssertionError("非法域名不应创建会话发起请求")

        monkeypatch.setattr("subtitle_utils.aiohttp.ClientSession", _TripwireSession)
        with pytest.raises(BvidParseError):
            asyncio.run(resolve_b23("https://example.com/abc"))


class TestResolveB23HopByHop:
    """#22 逐跳优化：重定向链中途 URL 已含 BV 号即停止，不再请求后续页面"""

    @staticmethod
    def _make_chain_session(locations):
        """按请求顺序返回 Location 的桩会话；None 表示该跳无 Location。
        返回 (FakeSession 类, 实际发出的请求 URL 列表)。"""
        requested = []

        class FakeResponse:
            def __init__(self, location):
                self.headers = {"Location": location} if location else {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def get(self, url, *args, **kwargs):
                requested.append(str(url))
                index = len(requested) - 1
                location = locations[index] if index < len(locations) else None
                return FakeResponse(location)

        return FakeSession, requested

    def test_bv_in_redirect_target_stops_early(self, monkeypatch):
        """第 1 跳目标已含 BV 号：不再请求视频页本身，直接返回 BVID"""
        session_cls, requested = self._make_chain_session(
            [
                "https://www.bilibili.com/video/BV1GJ411x7h7/",
                # 若误请求第 2 跳（整页下载）即暴露
                "https://www.bilibili.com/video/BV1GJ411x7h7/?spm_id_from=1",
            ]
        )
        monkeypatch.setattr("subtitle_utils.aiohttp.ClientSession", session_cls)
        assert asyncio.run(resolve_b23("https://b23.tv/4bdIZBf")) == "BV1GJ411x7h7"
        assert requested == ["https://b23.tv/4bdIZBf"]

    def test_bv_in_initial_url_no_request(self, monkeypatch):
        """初始 URL 已含 BV 号（b23.tv/BV… 形式）：一次请求都不发"""
        session_cls, requested = self._make_chain_session([])
        monkeypatch.setattr("subtitle_utils.aiohttp.ClientSession", session_cls)
        assert asyncio.run(resolve_b23("https://b23.tv/BV1GJ411x7h7")) == "BV1GJ411x7h7"
        assert requested == []

    def test_chain_without_bv_raises(self, monkeypatch):
        """整条链都没出现 BV 号：抛 BvidParseError"""
        session_cls, requested = self._make_chain_session(
            [
                "https://www.bilibili.com/not-a-video",
                "https://www.bilibili.com/still-no-bv",
                None,
            ]
        )
        monkeypatch.setattr("subtitle_utils.aiohttp.ClientSession", session_cls)
        with pytest.raises(BvidParseError):
            asyncio.run(resolve_b23("https://b23.tv/4bdIZBf"))
        assert len(requested) == 3


class TestFetchSubtitlePage:
    """多分 P 支持：page 参数透传、越界拒绝、标题标注。

    bilibili_api.video.Video 与字幕下载 aiohttp 会话均用桩替代，不触网。
    """

    SUBTITLE_INFO = {
        "subtitles": [
            {"lan": "zh-CN", "subtitle_url": "https://aisubtitle.hdslb.com/test.json"}
        ]
    }

    @staticmethod
    def _make_fake_video(pages):
        class FakeVideo:
            cid_calls = []

            def __init__(self, bvid, credential=None):
                self.bvid = bvid

            async def get_info(self):
                return {"title": "测试视频", "pages": pages}

            async def get_cid(self, page_index):
                type(self).cid_calls.append(page_index)
                return pages[page_index]["cid"]

            async def get_subtitle(self, cid):
                return TestFetchSubtitlePage.SUBTITLE_INFO

        return FakeVideo

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {"body": [{"content": "hello"}]}

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return TestFetchSubtitlePage._FakeResponse()

    def _patch(self, monkeypatch, pages):
        fake_video = self._make_fake_video(pages)
        monkeypatch.setattr("subtitle_utils.video.Video", fake_video)
        monkeypatch.setattr("subtitle_utils.aiohttp.ClientSession", self._FakeSession)
        return fake_video

    def test_single_page_no_annotation(self, monkeypatch):
        """单 P 视频默认路径：标题不带分 P 标注"""
        self._patch(monkeypatch, [{"cid": 111}])
        title, text = asyncio.run(fetch_subtitle("BV1GJ411x7h7", "sess", "jct"))
        assert title == "测试视频"
        assert "hello" in text

    def test_multi_page_p2_passthrough_and_annotation(self, monkeypatch):
        """多分 P 取 P2：get_cid 收到 0 起始下标，标题带 (P2) 标注"""
        fake_video = self._patch(
            monkeypatch, [{"cid": 111}, {"cid": 222}, {"cid": 333}]
        )
        title, _text = asyncio.run(
            fetch_subtitle("BV1GJ411x7h7", "sess", "jct", page=2)
        )
        assert fake_video.cid_calls == [1]
        assert title == "测试视频 (P2)"

    def test_page_out_of_range_rejected(self, monkeypatch):
        """越界分 P 返回友好错误而非崩溃"""
        self._patch(monkeypatch, [{"cid": 111}])
        with pytest.raises(SubtitleFetchError) as exc_info:
            asyncio.run(fetch_subtitle("BV1GJ411x7h7", "sess", "jct", page=2))
        assert "没有第 2 个分 P" in str(exc_info.value)
        assert "共 1 个分 P" in str(exc_info.value)

    def test_page_zero_rejected(self, monkeypatch):
        """page < 1 直接拒绝（LLM 可能传 0）"""
        self._patch(monkeypatch, [{"cid": 111}])
        with pytest.raises(SubtitleFetchError) as exc_info:
            asyncio.run(fetch_subtitle("BV1GJ411x7h7", "sess", "jct", page=0))
        assert "从 1 开始" in str(exc_info.value)
