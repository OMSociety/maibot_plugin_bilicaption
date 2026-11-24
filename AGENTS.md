# AGENTS.md

MaiBot 插件：读取 B 站视频字幕，向 LLM 暴露工具 `bili_view`。AstrBot 姊妹仓库 `astrbot_plugin_bilicaption` 与本仓库共用同一套字幕逻辑。

## 验收命令

```
pip install -r requirements-dev.txt
ruff format .
ruff check .
pytest
python <maibot-plugin-creator>/scripts/check_plugin.py .
```

- 四项全绿才算通过；`pytest` 需要已安装 `maibot-sdk`，否则 `tests/test_config_i18n.py` 整模块跳过。
- 系统临时目录不可写时给 `pytest` 加 `--basetemp=<可写目录>`。

## 目录职责

- `plugin.py`：配置模型、`bili_view` 工具、配置页扫码控件；只做框架适配。
- `subtitle_utils.py`：链接解析、字幕获取、缓存、长度保护；不得 import `maibot_sdk`。
- `qr_login.py`：扫码登录与凭据存储；不得 import `maibot_sdk`。
- `tests/`：纯逻辑测试以桩替代网络，不得发起真实请求。

## 规则

- 改 `subtitle_utils.py` → 同步更新 AstrBot 仓库的同名文件并分别跑两边测试；原因：两端各存一份，函数签名与行为必须一致。
- 改配置字段 → 同步改三语 i18n（`zh-CN` / `en-US` / `ja-JP`）与 `tests/test_config_i18n.py` 的字段数断言；通过标准：该测试全绿。
- 凡对用户可见的字幕失败原因，必须是 `SubtitleFetchError` / `BvidParseError` 的消息；原因：工具直接把 `str(e)` 返回给 LLM，不得吞异常或返回笼统文案。
- 每一跳短链重定向都必须过 `_is_safe_b23_url`；原因：重定向目标可被诱导到内网（SSRF）。
- 登录凭据只存 `ctx.paths.data_dir/credential.json`；禁止写入 `config.toml`、日志或测试夹具。
- `_manifest.json` 的 `version`、`plugin.config_version` 默认值与 `CHANGELOG.md` 最新条目同步修改；`config.toml` 已在 `.gitignore`，不得提交。
- 配置页运行时控件：`get_config_widget_state` 必须覆盖 `config_widgets` 声明的全部 id，单次预算 8 秒。
- `README.md` 与 `CHANGELOG.md` 不使用 emoji；`CHANGELOG.md` 中文在上英文在下，条目一一对应。
