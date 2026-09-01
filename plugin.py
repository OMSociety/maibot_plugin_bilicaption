"""MaiBot Plugin: BiliCaption — B站字幕提取解读

从 AstrBot 插件 astrbot_plugin_bilicaption 迁移（AGPL-3.0，继承上游 BiliRead）。
提供两个 LLM 工具：
- bilibili_caption：提取字幕纯文本（始终启用）
- bilibili_read：完整字幕通读，供 bot 深度解读（默认关闭，配置 enable_read_tool 启用）
"""

from typing import Any

from maibot_sdk import Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .subtitle_utils import (
    SubtitleFetchError,
    _truncate,
    fetch_subtitle,
    normalize_bvid,
)


class BiliCookieConfig(PluginConfigBase):
    """B站 Cookie 配置分组"""

    __ui_label__ = "B站Cookie"

    sessdata: str = Field(
        default="", description="B 站 SESSDATA Cookie（字幕接口需要登录态）"
    )
    bili_jct: str = Field(default="", description="B 站 bili_jct Cookie")


class PluginBaseConfig(PluginConfigBase):
    """插件基础配置"""

    __ui_label__ = "插件基础设置"

    config_version: str = Field(default="1.0.0", description="配置版本号")
    enabled: bool = Field(default=True, description="是否启用插件")


class ReadSettingsConfig(PluginConfigBase):
    """读取设置"""

    __ui_label__ = "读取设置"

    max_subtitle_length: int = Field(
        default=0,
        description="bilibili_caption 字幕最大返回长度（字符数），0表示不限制",
    )
    auto_send_txt: bool = Field(
        default=False,
        description="是否自动将字幕保存为txt文件并发送到聊天中（MaiBot 版暂不支持文件推送，此开关暂无效）",
    )
    enable_read_tool: bool = Field(
        default=False,
        description="是否启用 bilibili_read 深度解读工具（高token消耗）",
    )
    read_max_subtitle_length: int = Field(
        default=0,
        description="bilibili_read 工具的字幕最大返回长度（字符数），0表示不限制（全文通读）",
    )


class BiliCaptionConfig(PluginConfigBase):
    """插件完整配置"""

    __ui_label__ = "BiliCaption 配置"

    plugin: PluginBaseConfig = Field(
        default_factory=PluginBaseConfig, description="插件基础配置"
    )
    bilibili_cookie: BiliCookieConfig = Field(
        default_factory=BiliCookieConfig, description="B站Cookie"
    )
    read_settings: ReadSettingsConfig = Field(
        default_factory=ReadSettingsConfig, description="读取设置"
    )


class BiliCaptionPlugin(MaiBotPlugin):
    """B站字幕提取解读插件"""

    config_model = BiliCaptionConfig

    async def on_load(self) -> None:
        self.ctx.logger.info("BiliCaption 插件已加载")
        if not self.config.bilibili_cookie.sessdata:
            self.ctx.logger.warning(
                "SESSDATA 未配置：B 站字幕接口需要登录态，可能无法获取 AI 字幕"
            )
        # bilibili_read 默认关闭：未启用时禁用该组件，使其不出现在 LLM 工具列表
        if not self.config.read_settings.enable_read_tool:
            try:
                await self.ctx.component.disable_component(
                    "bilibili_read", "tool", scope="global"
                )
                self.ctx.logger.info("bilibili_read 未启用，已禁用该工具组件")
            except Exception as e:  # noqa: BLE001 - 组件禁用失败不阻塞加载
                self.ctx.logger.warning(f"禁用 bilibili_read 失败: {e}")

    async def on_unload(self) -> None:
        self.ctx.logger.info("BiliCaption 插件已卸载")

    async def on_config_update(
        self, scope: str, config_data: dict[str, Any], version: str
    ) -> None:
        if scope != "self":
            return
        # 配置热更新后同步 bilibili_read 的开关状态
        try:
            if self.config.read_settings.enable_read_tool:
                await self.ctx.component.enable_component(
                    "bilibili_read", "tool", scope="global"
                )
                self.ctx.logger.info("bilibili_read 已启用")
            else:
                await self.ctx.component.disable_component(
                    "bilibili_read", "tool", scope="global"
                )
                self.ctx.logger.info("bilibili_read 已禁用")
        except Exception as e:  # noqa: BLE001 - 开关同步失败不影响主流程
            self.ctx.logger.warning(f"同步 bilibili_read 开关失败: {e}")

    async def _fetch_subtitle(self, bvid_raw: str) -> tuple[str, str]:
        """获取并规范化字幕。返回 (title, subtitle_text)。"""
        bvid_raw = (bvid_raw or "").strip()
        if not bvid_raw:
            raise ValueError("请提供要获取字幕的 B 站视频链接、BV 号或 b23.tv 短链。")
        bvid = await normalize_bvid(bvid_raw)
        if bvid == "error":
            raise ValueError(
                "解析视频链接失败，请检查链接是否正确（支持 B 站完整链接 / BV 号 / b23.tv 短链）。"
            )
        return await fetch_subtitle(
            bvid,
            self.config.bilibili_cookie.sessdata,
            self.config.bilibili_cookie.bili_jct,
        )

    def _error_result(self, e: Exception) -> dict:
        """把异常转成 LLM 可读的错误结果"""
        return {"success": False, "message": str(e)}

    @Tool(
        "bilibili_caption",
        description="获取哔哩哔哩视频的字幕纯文本。如果视频没有字幕则返回提示信息。",
        brief_description="获取B站视频字幕纯文本",
        detailed_description=(
            "参数说明：\n"
            "- bvid：string，必填。BVID 或 b23.tv 链接，例如 BV1GJ411x7h7 或 https://b23.tv/4bdIZBf。"
        ),
        parameters=[
            ToolParameterInfo(
                name="bvid",
                param_type=ToolParamType.STRING,
                description="想要获取的哔哩哔哩视频的BVID或是b23.tv链接，例如BV1GJ411x7h7或https://b23.tv/4bdIZBf",
                required=True,
            ),
        ],
    )
    async def handle_caption(self, bvid: str, **kwargs):
        """字幕提取工具：返回字幕纯文本，供展示给用户。"""
        try:
            title, subtitle_text = await self._fetch_subtitle(bvid)
        except (ValueError, SubtitleFetchError) as e:
            return self._error_result(e)

        subtitle_text = _truncate(subtitle_text, self.config.read_settings.max_subtitle_length)
        if self.config.read_settings.auto_send_txt:
            self.ctx.logger.warning(
                "auto_send_txt 在 MaiBot 版暂不支持文件推送，已降级为纯文本返回"
            )
        return {"success": True, "content": f"[字幕] {title}\n\n{subtitle_text}"}

    @Tool(
        "bilibili_read",
        description=(
            "通读哔哩哔哩视频的完整字幕以便你解读视频内容。"
            "当用户要求你解读、总结、分析、评价某个B站视频时调用，"
            "返回完整字幕原文供你通读，之后由你自行组织语言输出解读。"
            "注意：完整字幕会占用大量上下文，token 消耗较高，"
            "仅在用户明确要求深度解读视频内容时调用，普通字幕提取请使用 bilibili_caption。"
        ),
        brief_description="通读B站视频完整字幕以便深度解读",
        detailed_description=(
            "参数说明：\n"
            "- bvid：string，必填。BVID 或 b23.tv 链接，例如 BV1GJ411x7h7 或 https://b23.tv/4bdIZBf。"
        ),
        parameters=[
            ToolParameterInfo(
                name="bvid",
                param_type=ToolParamType.STRING,
                description="想要解读的哔哩哔哩视频的BVID或是b23.tv链接，例如BV1GJ411x7h7或https://b23.tv/4bdIZBf",
                required=True,
            ),
        ],
    )
    async def handle_read(self, bvid: str, **kwargs):
        """深度解读工具：返回完整字幕原文，由 bot 自行阅读后解读。"""
        try:
            title, subtitle_text = await self._fetch_subtitle(bvid)
        except (ValueError, SubtitleFetchError) as e:
            return self._error_result(e)

        subtitle_text = _truncate(subtitle_text, self.config.read_settings.read_max_subtitle_length)
        return {"success": True, "content": f"[完整字幕] {title}\n\n{subtitle_text}"}


def create_plugin():
    return BiliCaptionPlugin()
