"""配置界面 i18n（en-US / ja-JP）回归测试

验证插件配置 Schema 的多语言元数据：
1. 字段级：凡含 label/hint/placeholder 的字段，i18n 必须覆盖 en-US 与 ja-JP。
2. 节级：凡含 title/description 的配置节，i18n 必须覆盖 en-US 与 ja-JP。
3. 纪律检测：译文数字与 base 一致、技术标识符不丢失、ja 汉字须伴随假名等。

依赖 maibot_sdk（未安装时整模块 skip，不影响其他测试）。
"""

import json
import os
import re
import sys

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.dirname(PLUGIN_DIR)
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

try:
    import maibot_sdk  # noqa: F401
except ImportError:
    pytest.skip("maibot_sdk not available", allow_module_level=True)

import maibot_plugin_bilicaption.plugin as plugin  # noqa: E402
from maibot_sdk.config import generate_plugin_config_schema  # noqa: E402

LOCALES = ("en-US", "ja-JP")
FIELD_I18N_KEYS = {"label", "hint", "placeholder"}
SECTION_I18N_KEYS = {"title", "description"}

# 白名单：base token -> 译文中允许的等价形式（术语表映射 / 单字母产品名）
TOKEN_WHITELIST = {
    "token": {"トークン"},
    "B": {"Bilibili"},  # “B 站”译为 Bilibili
}

DIGIT_RE = re.compile(r"\d+")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def _build_schema():
    return generate_plugin_config_schema(
        plugin.BiliCaptionConfig,
        plugin_id="omsociety.bilicaption",
    )


@pytest.fixture(scope="module")
def schema():
    return _build_schema()


def _iter_schema_sections(schema):
    """返回 [(section_name, section_schema, field_schemas)]"""
    return [
        (name, sec, sec.get("fields") or {}) for name, sec in schema["sections"].items()
    ]


def _field_base_texts(field_schema):
    """字段的 i18n 基准文本：label / hint / placeholder（hint 缺失时回退 description）。"""
    base = {}
    if field_schema.get("label"):
        base["label"] = field_schema["label"]
    if field_schema.get("hint"):
        base["hint"] = field_schema["hint"]
    elif field_schema.get("description"):
        base["hint"] = field_schema["description"]
    if field_schema.get("placeholder"):
        base["placeholder"] = field_schema["placeholder"]
    return base


class TestSectionI18nCoverage:
    """节级 i18n 覆盖测试"""

    def test_three_sections_rendered(self, schema):
        """三个嵌套配置类都应渲染为配置节（无 general 兜底节）"""
        assert sorted(schema["sections"].keys()) == [
            "bilibili_cookie",
            "plugin",
            "read_settings",
        ]

    def test_section_title_description_covered(self, schema):
        """凡含 title/description 的节，i18n 必须全 locale 覆盖"""
        checked = 0
        for name, sec, _fields in _iter_schema_sections(schema):
            base = {k: sec[k] for k in SECTION_I18N_KEYS if sec.get(k)}
            if not base:
                continue
            checked += 1
            i18n = sec.get("i18n") or {}
            for locale in LOCALES:
                entries = i18n.get(locale)
                assert isinstance(entries, dict), f"节 {name} 缺少 i18n[{locale}]"
                for key in base:
                    text = entries.get(key)
                    assert isinstance(text, str) and text, (
                        f"节 {name} i18n[{locale}] 缺少 {key}"
                    )
            for locale in LOCALES:
                extra = set((i18n.get(locale) or {}).keys()) - set(base)
                assert not extra, f"节 {name} i18n[{locale}] 存在多余键: {extra}"
        assert checked == 3


class TestFieldI18nCoverage:
    """字段级 i18n 覆盖测试"""

    def test_all_fields_covered(self, schema):
        """凡含 label/hint/placeholder 的字段，i18n 必须全 locale 覆盖"""
        checked = 0
        for name, _sec, fields in _iter_schema_sections(schema):
            for field_name, fs in fields.items():
                base = _field_base_texts(fs)
                assert "label" in base, f"字段 {field_name} 缺少 label"
                checked += 1
                i18n = fs.get("i18n") or {}
                for locale in LOCALES:
                    entries = i18n.get(locale)
                    assert isinstance(entries, dict), (
                        f"字段 {field_name} 缺少 i18n[{locale}]"
                    )
                    for key in base:
                        text = entries.get(key)
                        assert isinstance(text, str) and text, (
                            f"字段 {field_name} i18n[{locale}] 缺少 {key}"
                        )
                    extra = set(entries.keys()) - FIELD_I18N_KEYS
                    assert not extra, (
                        f"字段 {field_name} i18n[{locale}] 存在多余键: {extra}"
                    )
        assert checked == 7


class TestTranslationDiscipline:
    """翻译纪律检测"""

    @staticmethod
    def _iter_translations(schema):
        """产出 (path, key, base_text, locale, translated_text)"""
        for name, sec, fields in _iter_schema_sections(schema):
            base = {k: sec[k] for k in SECTION_I18N_KEYS if sec.get(k)}
            i18n = sec.get("i18n") or {}
            for key, base_text in base.items():
                for locale in LOCALES:
                    yield (
                        f"section:{name}",
                        key,
                        base_text,
                        locale,
                        (i18n.get(locale) or {}).get(key, ""),
                    )
            for field_name, fs in fields.items():
                f_base = _field_base_texts(fs)
                f_i18n = fs.get("i18n") or {}
                for key, base_text in f_base.items():
                    for locale in LOCALES:
                        yield (
                            f"{name}.{field_name}",
                            key,
                            base_text,
                            locale,
                            (f_i18n.get(locale) or {}).get(key, ""),
                        )

    def test_digits_match_base(self, schema):
        """译文中的数字集合必须与 base 一致（翻错会误导用户设错值）"""
        for path, key, base_text, locale, text in self._iter_translations(schema):
            assert set(DIGIT_RE.findall(base_text)) == set(DIGIT_RE.findall(text)), (
                f"{path}::{key}::{locale} 数字不一致: "
                f"base={DIGIT_RE.findall(base_text)} got={DIGIT_RE.findall(text)}"
            )

    def test_identifier_tokens_preserved(self, schema):
        """snake_case / UPPER / 驼峰 token 不丢失（可经白名单映射）"""
        for path, key, base_text, locale, text in self._iter_translations(schema):
            base_toks = [
                t
                for t in TOKEN_RE.findall(base_text)
                if "_" in t
                or (len(t) >= 2 and t.isupper())
                or re.search(r"[a-z][A-Z]", t)
            ]
            for tok in base_toks:
                allowed = {tok} | TOKEN_WHITELIST.get(tok, set())
                assert any(a in text for a in allowed), (
                    f"{path}::{key}::{locale} 丢失 token {tok!r} (译文: {text})"
                )

    def test_ja_kanji_requires_kana(self, schema):
        """ja 译文连续汉字≥5 且全串无假名时需报警（合法日语复合词除外）"""
        for path, key, _base_text, locale, text in self._iter_translations(schema):
            if locale != "ja-JP" or not text:
                continue
            runs = [r.group(0) for r in CJK_RUN_RE.finditer(text)]
            longest = max(runs, key=len) if runs else ""
            if len(longest) >= 5:
                assert KANA_RE.search(text), (
                    f"{path}::{key}::ja-JP 长汉字串无假名: {longest!r} (译文: {text})"
                )

    def test_en_no_cjk_residual(self, schema):
        """en 译文不应残留 CJK 字符"""
        for path, key, _base_text, locale, text in self._iter_translations(schema):
            if locale == "en-US" and text:
                assert not re.search(r"[\u4e00-\u9fff]", text), (
                    f"{path}::{key}::en-US 残留 CJK: {text}"
                )

    def test_ja_not_identical_to_base_unless_technical(self, schema):
        """ja 译文与 base 逐字相等仅允许纯技术串（SESSDATA / bili_jct）"""
        allowed_technical = {"SESSDATA", "bili_jct"}
        for path, key, base_text, locale, text in self._iter_translations(schema):
            if locale == "ja-JP" and text and text == base_text:
                assert base_text in allowed_technical, (
                    f"{path}::{key}::ja-JP 与 base 逐字相等且非纯技术串: {base_text!r}"
                )


class TestManifestI18n:
    """_manifest.json i18n 声明测试"""

    def test_supported_locales_declared(self):
        with open(os.path.join(PLUGIN_DIR, "_manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        i18n = manifest["i18n"]
        assert i18n["default_locale"] == "zh-CN"
        assert i18n["supported_locales"] == ["zh-CN", "en-US", "ja-JP"]
