"""
B 站扫码登录与凭据存储（与 MaiBot 框架无关的纯逻辑，便于独立测试）。

接口为 B 站 Web 端公开的扫码登录流程：
    GET https://passport.bilibili.com/x/passport-login/web/qrcode/generate
    GET https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=...
poll 返回码：0 登录成功、86101 未扫码、86090 已扫码待确认、86038 二维码过期。
"""

import base64
import io
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
import qrcode
from bilibili_api import Credential

logger = logging.getLogger(__name__)

QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# 二维码有效期（秒）；B 站二维码约 180 秒后过期，留出余量提前刷新
QR_LIFETIME_SECONDS = 170

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


class QrState(Enum):
    """扫码登录的状态。"""

    IDLE = "idle"
    WAITING_SCAN = "waiting_scan"
    WAITING_CONFIRM = "waiting_confirm"
    EXPIRED = "expired"
    SUCCESS = "success"
    ERROR = "error"


class QrLoginError(Exception):
    """扫码登录失败，异常消息可直接展示给用户。"""


@dataclass(frozen=True)
class QrTicket:
    """一次扫码会话：二维码内容、轮询密钥与过期时间（Unix 秒）。"""

    url: str
    key: str
    expires_at: float


@dataclass(frozen=True)
class PollResult:
    """一次轮询结果；仅 SUCCESS 时携带 cookies。"""

    state: QrState
    cookies: dict[str, str] | None = None


class CredentialStore:
    """登录凭据的本地文件存储（data_dir/credential.json，不进配置文件）。"""

    FIELDS = ("sessdata", "bili_jct", "dedeuserid", "ac_time_value")

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, str] | None:
        """读取凭据；文件不存在、损坏或缺少 sessdata 时返回 None。"""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            logger.warning(f"读取登录凭据失败: {type(e).__name__}")
            return None
        if not isinstance(raw, dict) or not raw.get("sessdata"):
            return None
        return {k: str(raw.get(k, "")) for k in self.FIELDS}

    def save(self, data: dict[str, str]) -> None:
        """原子写入凭据，文件权限收紧为仅所有者可读写。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {k: data.get(k, "") for k in self.FIELDS}, ensure_ascii=False
        )
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent, prefix=".credential-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)

    def credential(self) -> Credential | None:
        """构造 bilibili-api 的 Credential；无凭据返回 None。"""
        data = self.load()
        if data is None:
            return None
        return Credential(
            sessdata=data["sessdata"],
            bili_jct=data["bili_jct"],
            dedeuserid=data["dedeuserid"] or None,
            ac_time_value=data["ac_time_value"] or None,
        )


def make_qr_data_url(content: str) -> str:
    """把二维码内容渲染为 PNG data URL（配置页控件上限 1,000,000 字符）。"""
    img = qrcode.make(content, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


_COOKIE_FIELDS = {
    "sessdata": "SESSDATA",
    "bili_jct": "bili_jct",
    "dedeuserid": "DedeUserID",
}


def cookies_from_success_url(
    url: str, set_cookies: dict[str, str] | None = None
) -> dict[str, str]:
    """取出登录凭据：优先用成功跳转 URL 查询串，缺失的字段由响应 Set-Cookie 补齐。"""
    query = parse_qs(urlparse(url).query)
    extra = set_cookies or {}
    cookies = {
        k: (query.get(v) or [""])[0] or unquote(extra.get(v, ""))
        for k, v in _COOKIE_FIELDS.items()
    }
    if not cookies["sessdata"] or not cookies["bili_jct"]:
        raise QrLoginError("登录成功但响应中缺少 SESSDATA 或 bili_jct。")
    return cookies


def interpret_poll(
    data: dict,
    refresh_token: str = "",
    set_cookies: dict[str, str] | None = None,
) -> PollResult:
    """把 poll 接口 data 段映射为状态；未知返回码抛 QrLoginError。"""
    code = data.get("code")
    if code == 86101:
        return PollResult(QrState.WAITING_SCAN)
    if code == 86090:
        return PollResult(QrState.WAITING_CONFIRM)
    if code == 86038:
        return PollResult(QrState.EXPIRED)
    if code == 0:
        cookies = cookies_from_success_url(str(data.get("url", "")), set_cookies)
        cookies["ac_time_value"] = refresh_token or str(data.get("refresh_token", ""))
        return PollResult(QrState.SUCCESS, cookies)
    raise QrLoginError(f"B 站扫码登录返回未知状态码 {code}。")


async def _get_json(
    session: aiohttp.ClientSession, url: str, params: dict | None = None
) -> tuple[dict, dict[str, str]]:
    """GET 并解析 JSON，同时返回本次响应 Set-Cookie 的键值。"""
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise QrLoginError(f"B 站登录接口 HTTP 状态码 {resp.status}。")
            body = await resp.json(content_type=None)
            set_cookies = {k: m.value for k, m in resp.cookies.items()}
    except QrLoginError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.warning(f"B 站登录接口请求异常: {type(e).__name__}")
        raise QrLoginError("连接 B 站登录接口失败或超时，请稍后重试。") from e
    if not isinstance(body, dict):
        raise QrLoginError("B 站登录接口返回了无法识别的数据。")
    return body, set_cookies


async def generate_ticket(clock=time.time) -> QrTicket:
    """申请新的登录二维码。"""
    timeout = aiohttp.ClientTimeout(total=6)
    async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
        body, _ = await _get_json(session, QR_GENERATE_URL)
    data = body.get("data") or {}
    url, key = data.get("url"), data.get("qrcode_key")
    if body.get("code") != 0 or not url or not key:
        raise QrLoginError(f"申请登录二维码失败（返回码 {body.get('code')}）。")
    return QrTicket(url=url, key=key, expires_at=clock() + QR_LIFETIME_SECONDS)


async def poll_ticket(ticket: QrTicket) -> PollResult:
    """查询二维码当前的扫码状态。"""
    timeout = aiohttp.ClientTimeout(total=6)
    async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
        body, set_cookies = await _get_json(
            session, QR_POLL_URL, {"qrcode_key": ticket.key}
        )
    data = body.get("data")
    if body.get("code") != 0 or not isinstance(data, dict):
        raise QrLoginError(f"查询扫码状态失败（返回码 {body.get('code')}）。")
    return interpret_poll(data, set_cookies=set_cookies)


@dataclass(frozen=True)
class SessionView:
    """配置页展示用的会话快照。"""

    logged_in: bool
    uid: str
    state: QrState
    message: str
    ticket: QrTicket | None


class QrLoginSession:
    """扫码登录会话：持有当前二维码、轮询状态并在成功时写入凭据。

    网络函数与时钟可注入，便于测试。
    """

    def __init__(
        self,
        store: CredentialStore,
        generate=generate_ticket,
        poll=poll_ticket,
        clock=time.time,
    ) -> None:
        self._store = store
        self._generate = generate
        self._poll = poll
        self._clock = clock
        self._ticket: QrTicket | None = None
        self._state = QrState.IDLE
        self._message = ""

    async def start(self) -> None:
        """申请新二维码，替换进行中的会话。"""
        try:
            self._ticket = await self._generate()
        except QrLoginError as e:
            self._fail(str(e))
            return
        self._state = QrState.WAITING_SCAN
        self._message = ""

    def logout(self) -> None:
        self._store.clear()
        self._reset(QrState.IDLE)

    def _reset(self, state: QrState, message: str = "") -> None:
        self._ticket = None
        self._state = state
        self._message = message

    def _fail(self, message: str) -> None:
        self._reset(QrState.ERROR, message)

    async def view(self) -> SessionView:
        """推进一次会话并返回快照；已登录时不再发起网络请求。"""
        saved = self._store.load()
        if saved is not None:
            return SessionView(
                True, saved.get("dedeuserid", ""), QrState.SUCCESS, "", None
            )
        if self._ticket is not None:
            await self._advance(self._ticket)
            saved = self._store.load()
            if saved is not None:
                return SessionView(
                    True, saved.get("dedeuserid", ""), QrState.SUCCESS, "", None
                )
        return SessionView(False, "", self._state, self._message, self._ticket)

    async def _advance(self, ticket: QrTicket) -> None:
        if self._clock() >= ticket.expires_at:
            self._reset(QrState.EXPIRED, "二维码已过期，请重新获取。")
            return
        try:
            result = await self._poll(ticket)
        except QrLoginError as e:
            # 瞬时网络错误不废弃二维码，下次刷新继续轮询
            self._message = str(e)
            return
        if result.state is QrState.SUCCESS and result.cookies is not None:
            self._store.save(result.cookies)
            self._reset(QrState.SUCCESS)
        elif result.state is QrState.EXPIRED:
            self._reset(QrState.EXPIRED, "二维码已过期，请重新获取。")
        else:
            self._state = result.state
            self._message = ""
