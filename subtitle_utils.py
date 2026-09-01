"""
BiliCaption 核心逻辑模块（MaiBot 插件版）

存放与 MaiBot 框架无关的纯逻辑：B 站链接解析、字幕获取、文本清洗、
截断、文件名清理等，便于独立单元测试。plugin.py 通过 import 使用本模块。
原 astrbot.api.logger 已替换为标准库 logging（MaiBot 插件日志自动转发到主进程）。
"""

import logging
import re
from urllib.parse import urljoin, urlparse

import aiohttp
from bilibili_api import Credential, video
from bilibili_api.exceptions import CredentialNoSessdataException

logger = logging.getLogger(__name__)

# BVID 格式预编译正则：BV 开头，后接 10~12 位字母或数字
BVID_PATTERN = re.compile(r"BV[a-zA-Z0-9]{10,12}")

# b23.tv 短链域名（用于区分短链与普通链接，避免 BV 号被误判）
B23_HOSTS = ("b23.tv", "b23.wtf")


def _is_safe_b23_url(url: str) -> bool:
    """校验 b23 短链 / 重定向目标 URL 是否安全（仅公网 bilibili 域）。

    防 SSRF：仅放行 `b23.tv` / `b23.wtf` / `*.bilibili.com` 的 http(s) 地址，
    其余（内网 IP、localhost、云元数据、其他域名）一律拒绝。resolve_b23
    会跟随用户可控输入的 Location，因此每一跳都须过此校验。

    Args:
        url: 待校验的 URL。

    Returns:
        True 表示可安全请求。
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return host in B23_HOSTS or host == "bilibili.com" or host.endswith(".bilibili.com")


class SubtitleFetchError(Exception):
    """字幕获取失败，异常消息可直接作为工具结果返回给用户"""


def _clean_subtitle_text(raw: str) -> str:
    """清洗 B 站字幕正文：还原 <br> 换行、去除其余 HTML 标签、还原常见实体。

    Args:
        raw: 单条或拼接后的字幕文本。

    Returns:
        清洗后的纯文本；空输入返回空字符串。
    """
    if not raw:
        return ""

    # 先还原换行标签（B 站 AI 字幕用 <br> 表示换行）
    text = (
        raw.replace("<br>", "\n")
        .replace("</br>", "\n")
        .replace("<br/>", "\n")
        .replace("<br />", "\n")
    )
    # 再去除其余所有标签（如 <font color=...>），保留文字内容
    text = re.sub(r"<[^>]+>", "", text)
    # 还原常见 HTML 实体
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )
    # 逐行去空白并丢弃空行（含连续换行造成的空行）
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


async def resolve_b23(short_url: str) -> str:
    """解析 b23.tv 短链，返回其中的 BVID。失败返回 'error'。

    Args:
        short_url: b23.tv 短链（可带或不带 https:// 前缀）。

    Returns:
        提取到的 BVID；解析失败返回 "error"。
    """
    if not short_url.startswith("http"):
        short_url = "https://" + short_url

    # 初始 URL 必须是公网 b23 域（用户可控输入，防 SSRF）
    if not _is_safe_b23_url(short_url):
        logger.warning(f"拒绝非 b23 域的短链：{short_url}")
        return "error"

    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            real_url = short_url
            # 跟随重定向链，最多 10 跳
            for _ in range(10):
                async with session.get(real_url, allow_redirects=False) as response:
                    next_url = response.headers.get("Location")
                    if not next_url:
                        break
                    # 归一化：协议相对跳转（//…）等补全为绝对 URL 后再校验
                    next_url = urljoin(real_url, next_url)
                    # 每一跳目标也必须是公网 bilibili 域（防 SSRF：Location 可被诱导到内网）
                    if not _is_safe_b23_url(next_url):
                        logger.warning(f"拒绝短链重定向到非法域：{next_url}")
                        return "error"
                    real_url = next_url
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning(f"解析 b23.tv 短链网络异常：{short_url} -> {e}")
        return "error"

    match = BVID_PATTERN.search(real_url)
    if not match:
        logger.warning(f"解析 b23.tv 短链未找到 BV 号：{short_url} -> {real_url}")
        return "error"

    logger.info(f"解析 b23.tv 短链成功：{short_url} -> {match.group(0)}")
    return match.group(0)


async def normalize_bvid(raw: str) -> str:
    """规范化输入为 BVID，支持 BV 号、完整 B 站链接与 b23 短链。失败返回 'error'。

    兼容输入示例：
        BV1GJ411x7h7
        https://www.bilibili.com/video/BV1GJ411x7h7/
        https://b23.tv/4bdIZBf
        4bdIZBf（裸短码，兜底按 b23.tv 解析）

    Args:
        raw: 用户提供的原始链接或 BV 号。

    Returns:
        提取到的 BVID；解析失败返回 "error"。
    """
    raw = (raw or "").strip()
    if not raw:
        return "error"

    # 1. b23 短链：按域名判断，优先走短链解析
    lower = raw.lower()
    if any(host in lower for host in B23_HOSTS):
        return await resolve_b23(raw)

    # 2. 直接从文本中提取 BV 号（兼容完整链接 / 纯 BV 号 / 带前后缀的文本）
    match = BVID_PATTERN.search(raw)
    if match:
        return match.group(0)

    # 3. 兜底：兼容直接粘贴的短链代码（如 "4bdIZBf"）
    return await resolve_b23("https://b23.tv/" + raw)


async def fetch_subtitle(bvid: str, sessdata: str, bili_jct: str) -> tuple[str, str]:
    """获取视频标题与字幕全文，供各工具复用。

    Args:
        bvid: 视频 BVID。
        sessdata: B 站 SESSDATA Cookie（可为空字符串）。
        bili_jct: B 站 bili_jct Cookie（可为空字符串）。

    Returns:
        (title, subtitle_text) 元组。

    Raises:
        SubtitleFetchError: 网络异常、视频无字幕或解析失败时抛出，
            异常消息可直接展示给用户。
    """
    # B 站字幕接口需要登录态（AI 字幕对匿名用户隐藏），提前给出友好提示
    if not sessdata:
        raise SubtitleFetchError(
            "获取 B 站字幕需要登录态：请在插件配置的「B站Cookie」中填写 SESSDATA 与 bili_jct。"
        )

    credential = Credential(sessdata=sessdata, bili_jct=bili_jct)
    v = video.Video(bvid, credential=credential)

    try:
        # 1. 获取视频基础信息（可能抛网络异常或视频不存在的 API 异常）
        info = await v.get_info()
        title = info.get("title", "未知标题")

        # 2. 获取 CID
        cid = await v.get_cid(0)

        # 3. 获取字幕元数据
        subtitle_info = await v.get_subtitle(cid)
    except CredentialNoSessdataException as e:
        logger.warning(f"B 站 Cookie 无效（缺少 SESSDATA）: {bvid}")
        raise SubtitleFetchError(
            "B 站 Cookie 无效或已过期，请检查插件配置中的 SESSDATA 与 bili_jct。"
        ) from e
    except aiohttp.ClientError as e:
        logger.error(f"网络请求异常: {e}")
        raise SubtitleFetchError("网络请求异常，请稍后重试。") from e
    except Exception as e:
        logger.exception(f"获取视频信息失败: {bvid}")
        raise SubtitleFetchError("处理视频时发生内部错误，请稍后重试。") from e

    # 业务逻辑检查：是否有字幕数据
    if not subtitle_info or not subtitle_info.get("subtitles"):
        raise SubtitleFetchError(f"视频《{title}》暂无可用字幕。")

    # 优先寻找中文字幕 (zh-CN, zh-Hans)
    target_subtitle = None
    for sub in subtitle_info["subtitles"]:
        if sub.get("lan", "").startswith("zh"):
            target_subtitle = sub
            break

    # 兜底：取第一个
    if not target_subtitle:
        target_subtitle = subtitle_info["subtitles"][0]

    subtitle_url = target_subtitle.get("subtitle_url", "")
    if not subtitle_url:
        raise SubtitleFetchError("错误：字幕元数据中缺失 URL。")

    # 4. 下载字幕内容
    if not subtitle_url.startswith("http"):
        subtitle_url = "https:" + subtitle_url

    # 日志脱敏：去除 URL 参数，防止泄露签名
    log_url = subtitle_url.split("?")[0]
    logger.info(f"正在获取视频《{title}》字幕: {log_url}")

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(subtitle_url) as resp,
        ):
            if resp.status != 200:
                raise SubtitleFetchError(
                    f"下载字幕文件失败，HTTP 状态码: {resp.status}"
                )
            subtitle_json = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.error(f"网络请求异常: {e}")
        raise SubtitleFetchError("网络请求异常，请稍后重试。") from e

    # 5. 解析字幕正文并清洗 HTML 标签
    body = subtitle_json.get("body", [])
    raw_text = "\n".join(item.get("content", "") for item in body)
    raw_text = _clean_subtitle_text(raw_text)

    if not raw_text:
        raise SubtitleFetchError(f"视频《{title}》字幕内容解析为空。")

    return title, raw_text


def _truncate(text: str, max_len: int) -> str:
    """按上限截断字幕；max_len <= 0 表示不限制。

    Args:
        text: 原始文本。
        max_len: 最大字符数，<= 0 表示不截断。

    Returns:
        截断或原样的文本。
    """
    if max_len > 0 and len(text) > max_len:
        logger.info(f"字幕过长 ({len(text)}字符)，已执行截断。")
        return text[:max_len] + "\n...(后续内容已省略)"
    return text
