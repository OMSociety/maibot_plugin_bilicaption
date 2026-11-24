"""MaiBot Plugin: BiliCaption — B站视频字幕解读

提供一个 LLM 工具 bili_view：按 BV 号、av 号、完整链接或短链读取 B 站视频字幕，
供 bot 理解、总结、回答视频内容。登录凭据通过配置页扫码获得。
"""

from typing import Any, ClassVar

from maibot_sdk import (
    ConfigWidget,
    ConfigWidgetState,
    Field,
    MaiBotPlugin,
    PluginConfigBase,
    Tool,
)
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .qr_login import (
    CredentialStore,
    QrLoginSession,
    QrState,
    make_qr_data_url,
)
from .subtitle_utils import (
    SubtitleCache,
    SubtitleFetchError,
    load_subtitle,
    render_view,
)


def _field_extra(
    label: str,
    hint: str,
    en: tuple[str, str],
    ja: tuple[str, str],
    **extra: Any,
) -> dict[str, Any]:
    """生成带三语 i18n 的 json_schema_extra。"""
    return {
        "label": label,
        "hint": hint,
        "i18n": {
            "en-US": {"label": en[0], "hint": en[1]},
            "ja-JP": {"label": ja[0], "hint": ja[1]},
        },
        **extra,
    }


class PluginBaseConfig(PluginConfigBase):
    """插件基础配置"""

    __ui_label__ = "插件基础设置"

    __ui_i18n__: ClassVar[dict[str, dict[str, str]]] = {
        "en-US": {
            "title": "Plugin Base Settings",
            "description": "Basic plugin configuration",
        },
        "ja-JP": {"title": "プラグイン基本設定", "description": "プラグインの基本構成"},
    }

    config_version: str = Field(
        default="1.2.0",
        description="配置版本号",
        json_schema_extra=_field_extra(
            "配置版本",
            "配置版本号",
            ("Config version", "Configuration version number"),
            ("設定バージョン", "設定のバージョン番号"),
            disabled=True,
        ),
    )
    enabled: bool = Field(
        default=True,
        description="是否启用插件",
        json_schema_extra=_field_extra(
            "启用插件",
            "是否启用插件",
            ("Enable plugin", "Whether to enable the plugin"),
            ("プラグインを有効化", "プラグインを有効にするかどうか"),
        ),
    )


class ViewSettingsConfig(PluginConfigBase):
    """字幕展示设置"""

    __ui_label__ = "字幕展示"

    __ui_i18n__: ClassVar[dict[str, dict[str, str]]] = {
        "en-US": {
            "title": "Subtitle Display",
            "description": "Subtitle length protection",
        },
        "ja-JP": {"title": "字幕表示", "description": "字幕の長さ保護"},
    }

    max_chars: int = Field(
        default=20000,
        description="bili_view 单次返回的字幕最大字符数，0 表示不限制",
        json_schema_extra=_field_extra(
            "单次返回上限",
            "字符数，0=不限制；超出时保留开头与结尾，可用 offset 翻页读取中间部分",
            (
                "Per-call limit",
                "Characters; 0 = unlimited. Over-limit text keeps head and tail; page through with offset",
            ),
            (
                "一度に返す上限",
                "文字数。0 で無制限。超過時は先頭と末尾を残し、offset で続きを取得",
            ),
        ),
    )


class CacheSettingsConfig(PluginConfigBase):
    """字幕缓存设置"""

    __ui_label__ = "字幕缓存"

    __ui_i18n__: ClassVar[dict[str, dict[str, str]]] = {
        "en-US": {"title": "Subtitle Cache", "description": "In-memory subtitle cache"},
        "ja-JP": {"title": "字幕キャッシュ", "description": "メモリ内の字幕キャッシュ"},
    }

    ttl_seconds: int = Field(
        default=3600,
        description="字幕缓存有效期（秒），0 表示关闭缓存",
        json_schema_extra=_field_extra(
            "缓存有效期",
            "秒，0=关闭缓存",
            ("Cache TTL", "Seconds; 0 = cache disabled"),
            ("キャッシュ有効期間", "秒。0 でキャッシュ無効"),
        ),
    )
    max_entries: int = Field(
        default=64,
        description="字幕缓存最大条数，超出时淘汰最久未使用的条目",
        json_schema_extra=_field_extra(
            "缓存条数上限",
            "超出时淘汰最久未使用的条目",
            ("Max entries", "Least recently used entries are evicted when exceeded"),
            ("最大件数", "超過時は最も長く使われていない項目を破棄"),
        ),
    )


class BiliCaptionConfig(PluginConfigBase):
    """插件完整配置"""

    __ui_label__ = "BiliCaption 配置"

    plugin: PluginBaseConfig = Field(
        default_factory=PluginBaseConfig, description="插件基础配置"
    )
    view: ViewSettingsConfig = Field(
        default_factory=ViewSettingsConfig, description="字幕展示"
    )
    cache: CacheSettingsConfig = Field(
        default_factory=CacheSettingsConfig, description="字幕缓存"
    )


_WIDGET_ID_QR = "login_qr"
_WIDGET_ID_STATUS = "login_status"
_WIDGET_ID_START = "login_start"
_WIDGET_ID_LOGOUT = "login_logout"


class BiliCaptionPlugin(MaiBotPlugin):
    """B站视频字幕解读插件"""

    config_model = BiliCaptionConfig

    config_widgets: ClassVar[list[ConfigWidget]] = [
        ConfigWidget(id=_WIDGET_ID_QR, kind="qrcode", label="B站扫码登录"),
        ConfigWidget(id=_WIDGET_ID_STATUS, kind="status", label="登录状态"),
        ConfigWidget(id=_WIDGET_ID_START, kind="action", label="获取登录二维码"),
        ConfigWidget(
            id=_WIDGET_ID_LOGOUT,
            kind="action",
            label="退出登录",
            confirmation="确认删除已保存的 B 站登录凭据？",
        ),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._cache = SubtitleCache(3600, 64)
        self._login_session: QrLoginSession | None = None

    @property
    def _store(self) -> CredentialStore:
        return CredentialStore(self.ctx.paths.data_dir / "credential.json")

    @property
    def _login(self) -> QrLoginSession:
        if self._login_session is None:
            self._login_session = QrLoginSession(self._store)
        return self._login_session

    def _apply_cache_config(self) -> None:
        cache_cfg = self.config.cache
        self._cache.configure(cache_cfg.ttl_seconds, cache_cfg.max_entries)

    async def on_load(self) -> None:
        self._apply_cache_config()
        self.ctx.logger.info("BiliCaption 插件已加载")
        if self._store.load() is None:
            self.ctx.logger.warning(
                "尚未登录 B 站：请在插件配置页扫码登录，否则无法读取字幕"
            )

    async def on_unload(self) -> None:
        self._cache.clear()
        self._login_session = None
        self.ctx.logger.info("BiliCaption 插件已卸载")

    async def on_config_update(
        self, scope: str, config_data: dict[str, Any], version: str
    ) -> None:
        if scope != "self":
            return
        self._apply_cache_config()

    # ---- 配置页运行时控件：扫码登录 ----

    async def get_config_widget_state(self) -> dict[str, ConfigWidgetState]:
        view = await self._login.view()
        hidden = ConfigWidgetState(visible=False)
        if view.logged_in:
            self._cache.clear()
            return {
                _WIDGET_ID_QR: hidden,
                _WIDGET_ID_STATUS: ConfigWidgetState(
                    text=f"已登录（UID {view.uid or '未知'}）", tone="success"
                ),
                _WIDGET_ID_START: hidden,
                _WIDGET_ID_LOGOUT: ConfigWidgetState(),
            }

        qr = hidden
        text, tone = "未登录：点击“获取登录二维码”，用 B 站 App 扫码", "neutral"
        if view.ticket is not None:
            qr = ConfigWidgetState(
                image=make_qr_data_url(view.ticket.url),
                expires_at=view.ticket.expires_at,
            )
            if view.state is QrState.WAITING_CONFIRM:
                text, tone = "已扫码，请在手机上确认登录", "warning"
            else:
                text = "请使用 B 站 App 扫描二维码"
        if view.message:
            text, tone = view.message, "error" if view.ticket is None else "warning"
        return {
            _WIDGET_ID_QR: qr,
            _WIDGET_ID_STATUS: ConfigWidgetState(text=text, tone=tone),
            _WIDGET_ID_START: ConfigWidgetState(
                text="重新获取二维码" if view.ticket is not None else "获取登录二维码"
            ),
            _WIDGET_ID_LOGOUT: hidden,
        }

    async def on_config_widget_action(self, action_id: str) -> None:
        if action_id == _WIDGET_ID_START:
            await self._login.start()
        elif action_id == _WIDGET_ID_LOGOUT:
            self._login.logout()
            self._cache.clear()
            self.ctx.logger.info("已删除 B 站登录凭据")
        else:
            raise ValueError(f"未声明的配置页操作: {action_id}")

    # ---- 工具 ----

    @Tool(
        "bili_view",
        description=(
            "读取哔哩哔哩视频的字幕，用于理解、总结、评价或回答关于视频内容的问题。"
            "支持 BV 号、av 号、完整视频链接、b23.tv / bili2233.cn 短链，以及包含这些链接的分享文本。"
            "字幕过长时只返回开头与结尾，并在结果里给出 offset，用它继续读取中间部分。"
        ),
        brief_description="读取B站视频字幕以理解视频内容",
        detailed_description=(
            "参数说明：\n"
            "- video：string，必填。BV 号、av 号、视频链接或短链。\n"
            "- page：integer，可选。分 P 号，从 1 开始；链接已带 p 参数或单 P 视频无需传。\n"
            "- offset：integer，可选。从全文第 offset 个字符起连续读取一段；"
            "仅在上次结果提示字幕被省略、需要读取后续内容时传。"
        ),
        parameters=[
            ToolParameterInfo(
                name="video",
                param_type=ToolParamType.STRING,
                description="B 站视频的 BV 号、av 号、完整链接或 b23.tv 短链",
                required=True,
            ),
            ToolParameterInfo(
                name="page",
                param_type=ToolParamType.INTEGER,
                description="分 P 号，从 1 开始计数；仅当用户明确提到分 P 时才传。",
                required=False,
            ),
            ToolParameterInfo(
                name="offset",
                param_type=ToolParamType.INTEGER,
                description="从该字符位置起继续读取；仅在上次结果提示可继续读取时传。",
                required=False,
            ),
        ],
    )
    async def handle_view(
        self,
        video: str,
        page: int | None = None,
        offset: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """返回字幕文本窗口，由 bot 阅读后自行组织回答。"""
        try:
            page = int(page) if page is not None else None
            offset = int(offset) if offset is not None else None
        except (TypeError, ValueError):
            return {"success": False, "message": "page 与 offset 必须是整数。"}

        try:
            result = await load_subtitle(
                video, page, self._store.credential(), self._cache
            )
            content = render_view(
                result.title, result.text, self.config.view.max_chars, offset
            )
        except SubtitleFetchError as e:
            return {"success": False, "message": str(e)}
        return {"success": True, "content": content}


def create_plugin():
    return BiliCaptionPlugin()
