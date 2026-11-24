"""
BiliCaption 核心逻辑模块（MaiBot 插件版）

存放与 MaiBot 框架无关的纯逻辑：B 站链接解析（BV / av / 完整链接 / 短链）、
字幕获取、文本清洗、带过期清理的缓存、长度保护（首尾保留与 offset 分页），
便于独立单元测试。plugin.py 通过 import 使用本模块。
"""

import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import aiohttp
from bilibili_api import Credential, aid2bvid, video
from bilibili_api.exceptions import (
    NetworkException,
    ResponseCodeException,
)

logger = logging.getLogger(__name__)

# BVID 格式预编译正则：BV 开头，后接 10~12 位字母或数字
BVID_PATTERN = re.compile(r"BV[a-zA-Z0-9]{10,12}")
# av 号：av 前缀 + 数字，前后不得紧邻字母数字
AVID_PATTERN = re.compile(r"(?<![A-Za-z0-9])av(\d{1,12})(?![A-Za-z0-9])", re.IGNORECASE)
# 分 P 参数：链接查询串中的 p=N
PAGE_PATTERN = re.compile(r"[?&]p=(\d{1,4})(?![0-9])")

# 短链域名。b23.wtf 是第三方"去追踪参数"跳转服务、并非 B 站官方域名，
# 有意保留以兼容粘贴该类短链的用户；其每一跳仍须过 _is_safe_b23_url 域名校验。
B23_HOSTS = ("b23.tv", "b23.wtf", "bili2233.cn")
_SHORT_LINK_PATTERN = re.compile(
    r"(?<![\w.\-])(?:https?://)?("
    + "|".join(re.escape(h) for h in B23_HOSTS)
    + r")(/[^\s]*)?",
    re.IGNORECASE,
)

# 短链最多跟随的重定向跳数
MAX_REDIRECT_HOPS = 10
# 首尾保留模式下，开头部分占展示额度的比例
HEAD_RATIO = 0.6

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# B 站接口返回的常见业务码
_CODE_NOT_FOUND = (-404, 62002, 62004, 62012)
_CODE_RISK_CONTROL = (-412, -352, -799)


class SubtitleFetchError(Exception):
    """字幕获取失败，异常消息可直接作为工具结果返回给用户"""


class BvidParseError(SubtitleFetchError):
    """视频链接解析失败，异常消息可直接作为工具结果返回给用户"""


@dataclass(frozen=True)
class VideoRef:
    """解析出的视频引用：BVID 与分 P 号（链接未带分 P 时为 None）。"""

    bvid: str
    page: int | None = None


def _is_safe_b23_url(url: str) -> bool:
    """校验短链 / 重定向目标 URL 是否安全（仅公网 B 站相关域）。

    防 SSRF：仅放行 B23_HOSTS 与 `*.bilibili.com` 的 http(s) 地址，其余
    （内网 IP、localhost、云元数据、其他域名）一律拒绝。resolve_short_link
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


def _page_from_text(text: str) -> int | None:
    """取链接中的 p=N 分 P 号；不存在或为 0 时返回 None。"""
    m = PAGE_PATTERN.search(text)
    if not m:
        return None
    page = int(m.group(1))
    return page if page >= 1 else None


def _ref_from_text(text: str) -> VideoRef | None:
    """从文本中直接提取 BV 号或 av 号（不发起网络请求）。"""
    page = _page_from_text(text)
    m = BVID_PATTERN.search(text)
    if m:
        return VideoRef(m.group(0), page)
    m = AVID_PATTERN.search(text)
    if m:
        return VideoRef(aid2bvid(int(m.group(1))), page)
    return None


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


async def resolve_short_link(short_url: str) -> VideoRef:
    """解析短链，返回其中的视频引用。

    Args:
        short_url: 短链（可带或不带 https:// 前缀）。

    Returns:
        提取到的 VideoRef。

    Raises:
        BvidParseError: 非法域名、网络异常或重定向链中未找到视频号时抛出。
    """
    if not short_url.startswith("http"):
        short_url = "https://" + short_url

    # 初始 URL 必须是公网 B 站短链域（用户可控输入，防 SSRF）
    if not _is_safe_b23_url(short_url):
        logger.warning(f"拒绝非 B 站域的短链：{short_url}")
        raise BvidParseError("链接不是有效的 B 站短链（域名不在允许范围内）。")

    timeout = aiohttp.ClientTimeout(total=10)
    ref = _ref_from_text(short_url)
    real_url = short_url
    try:
        async with aiohttp.ClientSession(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        ) as session:
            hops = 0
            # 跟随重定向链；任一跳 URL 已含视频号即提前停止，不再请求最终页面
            while ref is None and hops < MAX_REDIRECT_HOPS:
                hops += 1
                async with session.get(real_url, allow_redirects=False) as response:
                    next_url = response.headers.get("Location")
                    if not next_url:
                        break
                    # 归一化：协议相对跳转（//…）等补全为绝对 URL 后再校验
                    next_url = urljoin(real_url, next_url)
                    # 每一跳目标也必须是公网 B 站域（防 SSRF：Location 可被诱导到内网）
                    if not _is_safe_b23_url(next_url):
                        logger.warning(f"拒绝短链重定向到非法域：{next_url}")
                        raise BvidParseError("短链重定向到了非 B 站域名，已拒绝访问。")
                    real_url = next_url
                    ref = _ref_from_text(real_url)
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning(f"解析短链网络异常：{short_url} -> {type(e).__name__}")
        raise BvidParseError("解析短链时网络异常或超时，请稍后重试。") from e

    if ref is None:
        logger.warning(f"解析短链未找到视频号：{short_url} -> {real_url}")
        raise BvidParseError("短链指向的页面不是视频（未找到 BV 号或 av 号）。")

    logger.info(f"解析短链成功：{short_url} -> {ref.bvid}")
    return ref


async def parse_video_ref(raw: str) -> VideoRef:
    """把用户输入解析为视频引用，支持 BV 号、av 号、完整链接与短链。

    兼容输入示例：
        BV1GJ411x7h7
        av170001 / 170001
        https://www.bilibili.com/video/BV1GJ411x7h7/?p=3
        https://b23.tv/4bdIZBf  /  https://bili2233.cn/xxxx
        4bdIZBf（裸短码，兜底按 b23.tv 解析）
        含上述任一形式的整段分享文本

    Args:
        raw: 用户提供的原始链接、编号或分享文本。

    Returns:
        VideoRef（链接中带 p 参数时含分 P 号）。

    Raises:
        BvidParseError: 输入为空或无法解析出视频号时抛出。
    """
    raw = (raw or "").strip()
    if not raw:
        raise BvidParseError("请提供 B 站视频链接、BV 号、av 号或短链。")

    # 1. 短链：按域名白名单匹配，匹配到才发起网络请求
    m = _SHORT_LINK_PATTERN.search(raw)
    if m:
        host = m.group(1).lower()
        path = (m.group(2) or "").rstrip("，。,.!！)）」】")
        return await resolve_short_link(f"https://{host}{path}")

    # 2. 直接从文本中提取 BV 号 / av 号（完整链接、纯编号、带前后缀的文本）
    ref = _ref_from_text(raw)
    if ref:
        return ref

    # 3. 纯数字按 av 号处理
    if re.fullmatch(r"\d{1,12}", raw):
        return VideoRef(aid2bvid(int(raw)))

    # 4. 兜底：兼容直接粘贴的短链代码（如 "4bdIZBf"）。
    # 字符集门禁：裸短码只可能是 5-13 位字母数字，其余输入（LLM 误传参的
    # 普通文本等）直接判失败，避免对 b23.tv 发起无意义的出站请求。
    if re.fullmatch(r"[A-Za-z0-9]{5,13}", raw):
        return await resolve_short_link("https://b23.tv/" + raw)
    raise BvidParseError(
        "无法识别的视频链接（支持 BV 号、av 号、bilibili.com 视频链接、b23.tv / bili2233.cn 短链）。"
    )


@dataclass(frozen=True)
class SubtitleResult:
    """字幕获取结果。"""

    title: str
    text: str


class SubtitleCache:
    """带过期与容量上限的字幕缓存。

    过期条目在每次读写时顺带清理；超出容量时淘汰最久未使用的条目。
    ttl_seconds <= 0 或 max_entries <= 0 表示禁用缓存。
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, SubtitleResult]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0 and self._max > 0

    def __len__(self) -> int:
        return len(self._items)

    def configure(self, ttl_seconds: float, max_entries: int) -> None:
        """热更新参数并立即按新参数清理。"""
        self._ttl = ttl_seconds
        self._max = max_entries
        if not self.enabled:
            self._items.clear()
            return
        self.purge()
        self._evict_overflow()

    def purge(self) -> int:
        """移除全部过期条目，返回移除数量。"""
        now = self._clock()
        expired = [k for k, (exp, _) in self._items.items() if exp <= now]
        for k in expired:
            del self._items[k]
        return len(expired)

    def _evict_overflow(self) -> None:
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def get(self, key: str) -> SubtitleResult | None:
        if not self.enabled:
            return None
        self.purge()
        entry = self._items.get(key)
        if entry is None:
            return None
        self._items.move_to_end(key)
        return entry[1]

    def put(self, key: str, value: SubtitleResult) -> None:
        if not self.enabled:
            return
        self.purge()
        self._items[key] = (self._clock() + self._ttl, value)
        self._items.move_to_end(key)
        self._evict_overflow()

    def clear(self) -> None:
        self._items.clear()


def _api_error(e: ResponseCodeException, bvid: str) -> SubtitleFetchError:
    """把 B 站业务码异常映射成可读的失败原因。"""
    if e.code in _CODE_NOT_FOUND:
        return SubtitleFetchError(
            f"视频 {bvid} 不存在、已被删除或暂不可见（B 站返回码 {e.code}）。"
        )
    if e.code in _CODE_RISK_CONTROL:
        return SubtitleFetchError(
            f"请求被 B 站风控拦截（返回码 {e.code}），请稍后再试。"
        )
    if e.code == -101:
        return SubtitleFetchError("B 站登录状态已失效，请在插件中重新登录。")
    logger.warning(f"B 站接口业务错误: bvid={bvid} code={e.code}")
    return SubtitleFetchError(f"B 站接口返回错误（返回码 {e.code}）。")


async def fetch_subtitle(
    bvid: str, credential: Credential | None, page: int = 1
) -> SubtitleResult:
    """获取视频标题与字幕全文。

    Args:
        bvid: 视频 BVID。
        credential: B 站登录凭据；None 表示未登录。
        page: 分 P 号，从 1 计数；单 P 视频无需传（默认 1）。

    Returns:
        SubtitleResult；多分 P 视频的 title 带 "(P{n})" 标注。

    Raises:
        SubtitleFetchError: 未登录、网络异常、分 P 越界、视频无字幕或解析失败时抛出，
            异常消息可直接展示给用户。
    """
    # B 站字幕接口需要登录态（AI 字幕对匿名用户隐藏），提前给出明确提示
    if credential is None:
        raise SubtitleFetchError("获取 B 站字幕需要登录：请先在插件中完成 B 站登录。")

    # 分 P 号从 1 计数，提前拒绝非法值（LLM 可能传 0 或负数）
    if page < 1:
        raise SubtitleFetchError("分 P 号从 1 开始，请提供有效的分 P 号。")

    v = video.Video(bvid, credential=credential)

    try:
        info = await v.get_info()
        title = info.get("title", "未知标题")

        pages = info.get("pages") or []
        page_count = len(pages) or 1
        if page > page_count:
            raise SubtitleFetchError(
                f"视频《{title}》没有第 {page} 个分 P（共 {page_count} 个分 P）。"
            )
        cid = await v.get_cid(page - 1)
        subtitle_info = await v.get_subtitle(cid)
    except SubtitleFetchError:
        raise
    except ResponseCodeException as e:
        raise _api_error(e, bvid) from e
    except (NetworkException, aiohttp.ClientError, TimeoutError) as e:
        logger.error(f"网络请求异常: {type(e).__name__}")
        raise SubtitleFetchError("网络请求异常或超时，请稍后重试。") from e
    except Exception as e:
        logger.exception(f"获取视频信息失败: {bvid}")
        raise SubtitleFetchError("处理视频时发生内部错误，请稍后重试。") from e

    display_title = f"{title} (P{page})" if page_count > 1 else title

    if not subtitle_info or not subtitle_info.get("subtitles"):
        # 登录失效时 B 站同样返回空字幕列表，需与「视频确实没有字幕」区分
        try:
            valid = await credential.check_valid()
        except Exception:  # noqa: BLE001 - 校验失败按「无法确认」处理，不阻断原因提示
            valid = True
        if not valid:
            raise SubtitleFetchError("B 站登录状态已失效，请在插件中重新登录。")
        raise SubtitleFetchError(
            f"视频《{display_title}》没有字幕（UP 主未上传，也没有 B 站 AI 字幕）。"
        )

    # 优先寻找中文字幕 (zh-CN, zh-Hans)，兜底取第一个
    target_subtitle = next(
        (s for s in subtitle_info["subtitles"] if s.get("lan", "").startswith("zh")),
        subtitle_info["subtitles"][0],
    )

    subtitle_url = target_subtitle.get("subtitle_url", "")
    if not subtitle_url:
        raise SubtitleFetchError(f"视频《{display_title}》的字幕元数据缺少下载地址。")
    if not subtitle_url.startswith("http"):
        subtitle_url = "https:" + subtitle_url

    # 日志脱敏：去除 URL 参数，防止泄露签名
    logger.info(f"正在获取视频《{title}》字幕: {subtitle_url.split('?')[0]}")

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(subtitle_url) as resp,
        ):
            if resp.status != 200:
                raise SubtitleFetchError(
                    f"下载字幕文件失败，HTTP 状态码: {resp.status}。"
                )
            subtitle_json = await resp.json()
    except SubtitleFetchError:
        raise
    except aiohttp.ContentTypeError as e:
        # CDN 偶发返回非 JSON（HTML 错误页等），单列以免落入笼统的"网络请求异常"
        logger.error(f"字幕内容解析失败（非 JSON 响应）: {e.message}")
        raise SubtitleFetchError(
            "字幕内容解析失败（CDN 返回了非字幕数据），请稍后重试。"
        ) from e
    except (aiohttp.ClientError, TimeoutError) as e:
        # 日志脱敏：ClientResponseError.__str__ 会带出含签名的完整字幕 URL，
        # 只记 message；连接类异常无 message 属性，回退到异常类名
        detail = getattr(e, "message", "") or type(e).__name__
        logger.error(f"下载字幕网络异常: {detail}")
        raise SubtitleFetchError("下载字幕时网络异常或超时，请稍后重试。") from e

    body = subtitle_json.get("body", [])
    raw_text = _clean_subtitle_text("\n".join(item.get("content", "") for item in body))
    if not raw_text:
        raise SubtitleFetchError(f"视频《{display_title}》的字幕内容为空。")

    return SubtitleResult(display_title, raw_text)


async def load_subtitle(
    video: str,
    page: int | None,
    credential: Credential | None,
    cache: SubtitleCache,
) -> SubtitleResult:
    """解析视频引用并取字幕，优先读缓存；page 为 None 时取链接中的分 P，缺省为 1。"""
    ref = await parse_video_ref(video)
    target_page = page if page is not None else (ref.page or 1)
    key = f"{ref.bvid}:{target_page}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = await fetch_subtitle(ref.bvid, credential, page=target_page)
    cache.put(key, result)
    return result


@dataclass(frozen=True)
class TextWindow:
    """一次展示的文本窗口与定位信息。"""

    head: str
    tail: str
    total: int
    start: int
    omitted_from: int
    omitted_to: int
    end: int
    next_offset: int | None

    @property
    def is_complete(self) -> bool:
        return self.start == 0 and self.end == self.total and not self.tail


def window_text(text: str, max_chars: int, offset: int | None = None) -> TextWindow:
    """按长度保护规则取文本窗口。

    - offset 为 None：全文不超过 max_chars 时整段返回；超过则保留开头与结尾，
      中间省略，next_offset 指向省略区起点。
    - offset 给定：返回从 offset 起连续的 max_chars 字符。
    - max_chars <= 0 表示不限制长度。

    Raises:
        SubtitleFetchError: offset 为负数或不小于全文长度。
    """
    total = len(text)
    if offset is not None:
        if offset < 0 or offset >= total:
            raise SubtitleFetchError(
                f"offset 超出范围：全文共 {total} 字符，offset 应在 0 到 {total - 1} 之间。"
            )
        end = total if max_chars <= 0 else min(total, offset + max_chars)
        return TextWindow(
            head=text[offset:end],
            tail="",
            total=total,
            start=offset,
            omitted_from=end,
            omitted_to=end,
            end=end,
            next_offset=end if end < total else None,
        )

    if max_chars <= 0 or total <= max_chars:
        return TextWindow(text, "", total, 0, total, total, total, None)

    head_len = max(1, int(max_chars * HEAD_RATIO))
    tail_len = max(1, max_chars - head_len)
    return TextWindow(
        head=text[:head_len],
        tail=text[total - tail_len :],
        total=total,
        start=0,
        omitted_from=head_len,
        omitted_to=total - tail_len,
        end=total,
        next_offset=head_len,
    )


def render_view(
    title: str, text: str, max_chars: int, offset: int | None = None
) -> str:
    """生成 bili_view 的返回正文：标题、定位说明与字幕窗口。"""
    w = window_text(text, max_chars, offset)

    if w.is_complete:
        return f"[字幕] {title}\n（全文 {w.total} 字符，已完整展示）\n\n{w.head}"

    if w.tail:
        omitted = w.omitted_to - w.omitted_from
        pct = round(omitted * 100 / w.total)
        note = (
            f"（全文 {w.total} 字符，超过展示上限，只展示开头 {len(w.head)} 与结尾 "
            f"{len(w.tail)} 字符，中间约 {pct}% 已省略；"
            f"如需中间部分，用 offset={w.omitted_from} 继续读取）"
        )
        gap = f"\n\n……（省略第 {w.omitted_from}–{w.omitted_to} 字符）……\n\n"
        return f"[字幕] {title}\n{note}\n\n{w.head}{gap}{w.tail}"

    if w.next_offset is None:
        tail_note = "已到全文末尾"
    else:
        tail_note = f"如需继续，用 offset={w.next_offset} 读取下一段"
    note = f"（全文 {w.total} 字符，当前为第 {w.start}–{w.end} 字符；{tail_note}）"
    return f"[字幕] {title}\n{note}\n\n{w.head}"
