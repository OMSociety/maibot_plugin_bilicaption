"""qr_login 状态机与凭据存储测试（网络以桩替代）。"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qr_login as q

SUCCESS_URL = (
    "https://passport.biligame.com/x/passport-login/web/crossDomain?"
    "DedeUserID=42&DedeUserID__ckMd5=x&SESSDATA=sess%2Cabc&bili_jct=jct123&gourl=x"
)


class TestInterpretPoll:
    def test_waiting_scan(self):
        assert q.interpret_poll({"code": 86101}).state is q.QrState.WAITING_SCAN

    def test_waiting_confirm(self):
        assert q.interpret_poll({"code": 86090}).state is q.QrState.WAITING_CONFIRM

    def test_expired(self):
        assert q.interpret_poll({"code": 86038}).state is q.QrState.EXPIRED

    def test_success_extracts_cookies(self):
        r = q.interpret_poll({"code": 0, "url": SUCCESS_URL, "refresh_token": "rt"})
        assert r.state is q.QrState.SUCCESS
        assert r.cookies == {
            "sessdata": "sess,abc",
            "bili_jct": "jct123",
            "dedeuserid": "42",
            "ac_time_value": "rt",
        }

    def test_success_missing_cookie(self):
        with pytest.raises(q.QrLoginError):
            q.interpret_poll({"code": 0, "url": "https://x/?DedeUserID=1"})

    def test_unknown_code(self):
        with pytest.raises(q.QrLoginError, match="1234"):
            q.interpret_poll({"code": 1234})


class TestStore:
    def test_roundtrip(self, tmp_path):
        s = q.CredentialStore(tmp_path / "d" / "credential.json")
        assert s.load() is None and s.credential() is None
        s.save(
            {"sessdata": "a", "bili_jct": "b", "dedeuserid": "1", "ac_time_value": "t"}
        )
        assert s.load()["sessdata"] == "a"
        cred = s.credential()
        assert cred.sessdata == "a" and cred.bili_jct == "b"
        s.clear()
        assert s.load() is None

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / "credential.json"
        p.write_text("{not json", encoding="utf-8")
        assert q.CredentialStore(p).load() is None

    def test_missing_sessdata(self, tmp_path):
        p = tmp_path / "credential.json"
        p.write_text('{"bili_jct": "b"}', encoding="utf-8")
        assert q.CredentialStore(p).load() is None

    def test_no_temp_leftover(self, tmp_path):
        s = q.CredentialStore(tmp_path / "credential.json")
        s.save({"sessdata": "a", "bili_jct": "b"})
        assert [p.name for p in tmp_path.iterdir()] == ["credential.json"]


def test_qr_data_url():
    url = q.make_qr_data_url("https://example.com")
    assert url.startswith("data:image/png;base64,") and len(url) < 1_000_000


class TestSession:
    def _session(self, tmp_path, polls, now=None):
        clock = now or [0.0]
        seq = iter(polls)

        async def gen():
            return q.QrTicket("https://u", "k", 100.0)

        async def poll(_ticket):
            item = next(seq)
            if isinstance(item, Exception):
                raise item
            return item

        store = q.CredentialStore(tmp_path / "credential.json")
        return q.QrLoginSession(store, gen, poll, lambda: clock[0]), store, clock

    def test_full_flow(self, tmp_path):
        ok = q.PollResult(
            q.QrState.SUCCESS,
            {"sessdata": "s", "bili_jct": "j", "dedeuserid": "7", "ac_time_value": ""},
        )
        s, store, _ = self._session(
            tmp_path,
            [
                q.PollResult(q.QrState.WAITING_SCAN),
                q.PollResult(q.QrState.WAITING_CONFIRM),
                ok,
            ],
        )

        async def run():
            assert (await s.view()).state is q.QrState.IDLE
            await s.start()
            assert (await s.view()).state is q.QrState.WAITING_SCAN
            assert (await s.view()).state is q.QrState.WAITING_CONFIRM
            v = await s.view()
            assert v.logged_in and v.uid == "7" and v.ticket is None
            assert store.load()["sessdata"] == "s"
            s.logout()
            assert not (await s.view()).logged_in

        asyncio.run(run())

    def test_expired_by_clock_and_by_server(self, tmp_path):
        s, _, clock = self._session(tmp_path, [q.PollResult(q.QrState.EXPIRED)])

        async def run():
            await s.start()
            v = await s.view()
            assert v.state is q.QrState.EXPIRED and v.ticket is None
            await s.start()
            clock[0] = 100.0
            v = await s.view()
            assert v.state is q.QrState.EXPIRED and "过期" in v.message

        asyncio.run(run())

    def test_transient_error_keeps_ticket(self, tmp_path):
        s, _, _ = self._session(
            tmp_path, [q.QrLoginError("网络"), q.PollResult(q.QrState.WAITING_SCAN)]
        )

        async def run():
            await s.start()
            v = await s.view()
            assert v.ticket is not None and v.message == "网络"
            v = await s.view()
            assert v.state is q.QrState.WAITING_SCAN and v.message == ""

        asyncio.run(run())

    def test_generate_failure(self, tmp_path):
        async def gen():
            raise q.QrLoginError("申请失败")

        s = q.QrLoginSession(q.CredentialStore(tmp_path / "c.json"), gen)

        async def run():
            await s.start()
            v = await s.view()
            assert v.state is q.QrState.ERROR and v.message == "申请失败"

        asyncio.run(run())


def test_cookies_fall_back_to_set_cookie_when_url_lacks_them():
    url = "https://passport.biligame.com/x/passport-login/web/crossDomain?DedeUserID=42&gourl=x"
    got = q.cookies_from_success_url(
        url, {"SESSDATA": "sess%2Cabc", "bili_jct": "jct123"}
    )
    assert got == {"sessdata": "sess,abc", "bili_jct": "jct123", "dedeuserid": "42"}


def test_url_values_take_precedence_over_set_cookie():
    url = "https://x/?SESSDATA=fromurl&bili_jct=j1"
    got = q.cookies_from_success_url(url, {"SESSDATA": "fromcookie"})
    assert got["sessdata"] == "fromurl"


def test_missing_everywhere_still_raises():
    with pytest.raises(q.QrLoginError):
        q.cookies_from_success_url("https://x/?DedeUserID=1", {})
