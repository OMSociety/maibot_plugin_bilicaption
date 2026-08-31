"""
BiliCaption 核心逻辑测试

直接测试 subtitle_utils.py 中的真实函数（不依赖网络与 AstrBot 环境）。
resolve_b23 等网络环节通过 monkeypatch 桩替代。
"""

import asyncio
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subtitle_utils import (
    _clean_subtitle_text,
    _sanitize_filename,
    _truncate,
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


class TestSanitizeFilename:
    """文件名清理测试"""

    def test_illegal_chars_replaced(self):
        """Windows 非法字符应被替换"""
        result = _sanitize_filename('a\\/:*?"<>|b')
        assert '\\/:*?"<>|' not in result

    def test_truncate_long_name(self):
        """超长名称应截断"""
        result = _sanitize_filename("A" * 100)
        assert len(result) <= 55  # 50 字符 + "..."
        assert result.endswith("...")

    def test_blank_fallback(self):
        """空白名称回退为 unknown"""
        assert _sanitize_filename("   ") == "unknown"

    def test_normal_name(self):
        """正常名称原样保留"""
        assert _sanitize_filename("我的视频") == "我的视频"


class TestNormalizeBvid:
    """链接规范化测试（resolve_b23 用桩替代，避免真实网络请求）"""

    @staticmethod
    async def _fake_resolve_ok(url: str) -> str:
        return "BV1GJ411x7h7"

    @staticmethod
    async def _fake_resolve_error(url: str) -> str:
        return "error"

    def test_pure_bvid(self, monkeypatch):
        """纯 BV 号直接识别，不走短链解析"""
        calls = []

        async def fake_resolve(url: str) -> str:
            calls.append(url)
            return "error"

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        assert asyncio.run(normalize_bvid("BV1GJ411x7h7")) == "BV1GJ411x7h7"
        assert calls == []

    def test_full_url(self, monkeypatch):
        """完整 B 站链接应提取出 BV 号（核心修复点）"""

        async def fake_resolve(url: str) -> str:
            return "error"

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
        """空输入返回 error"""

        async def fake_resolve(url: str) -> str:
            return "error"

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        assert asyncio.run(normalize_bvid("")) == "error"
        assert asyncio.run(normalize_bvid("   ")) == "error"

    def test_resolve_failure(self, monkeypatch):
        """短链解析失败时返回 error"""

        async def fake_resolve(url: str) -> str:
            return "error"

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        assert asyncio.run(normalize_bvid("https://b23.tv/4bdIZBf")) == "error"

    def test_bv_with_b23_substring_not_misjudged(self, monkeypatch):
        """BV 号含 b23 子串不应被误判为短链（核心修复点）"""
        calls = []

        async def fake_resolve(url: str) -> str:
            calls.append(url)
            return "error"

        monkeypatch.setattr("subtitle_utils.resolve_b23", fake_resolve)
        # BV1b2345678x 含 "b23" 但非短链，应被识别为 BV 号
        assert asyncio.run(normalize_bvid("BV1b2345678x")) == "BV1b2345678x"
        assert calls == []


class TestResolveB23ErrorHandling:
    """b23 短链解析的网络异常兜底测试"""

    def test_timeout_returns_error(self, monkeypatch):
        """超时（TimeoutError）应返回 'error' 而非崩溃"""

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
        assert asyncio.run(resolve_b23("https://b23.tv/abc")) == "error"

    def test_connection_error_returns_error(self, monkeypatch):
        """连接错误（ClientError）应返回 'error' 而非崩溃"""

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
        assert asyncio.run(resolve_b23("https://b23.tv/abc")) == "error"
