<div align="center">

<h1><img src="https://raw.githubusercontent.com/OMSociety/maibot_plugin_bilicaption/main/logo.png" width="192" alt="BiliCaption Logo"><br>BiliCaption B站视频字幕解读</h1>

**B 站视频字幕读取助手** —— 扫码登录 · 多格式链接识别 · 长度保护与翻页 · 字幕缓存

[![Version](https://img.shields.io/github/v/tag/OMSociety/maibot_plugin_bilicaption?label=version&color=blue)](https://github.com/OMSociety/maibot_plugin_bilicaption/releases)
[![MaiBot](https://img.shields.io/badge/MaiBot-%E2%89%A51.4.8-green.svg)](https://github.com/Mai-with-u/MaiBot)
[![License](https://img.shields.io/badge/license-AGPL--3.0-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/maibot_plugin_bilicaption)](https://github.com/OMSociety/maibot_plugin_bilicaption/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/maibot_plugin_bilicaption)](https://github.com/OMSociety/maibot_plugin_bilicaption/issues)

[核心特性](#核心特性) • [快速开始](#快速开始) • [配置项说明](#配置项说明) • [LLM 可调用工具](#llm-可调用工具) • [常见问题](#常见问题) • [更新日志](CHANGELOG.md)

</div>

> 本项目由 AI 编写 · 与 AstrBot 插件 [OMSociety/astrbot_plugin_bilicaption](https://github.com/OMSociety/astrbot_plugin_bilicaption) 共用同一套字幕逻辑 · 源码基于 [SodaCodeSave/astrbot_plugin_biliread](https://github.com/SodaCodeSave/astrbot_plugin_biliread) 二次开发

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **字幕读取** | 读取 B 站视频字幕原文，交给 bot 理解、总结、评价或回答视频相关问题 |
| **链接多格式识别** | BV 号 / av 号 / 完整链接（含 `?p=` 分 P）/ b23.tv、bili2233.cn 短链 / 整段分享文本 |
| **扫码登录** | 在 WebUI 插件配置页用 B 站 App 扫码登录，凭据保存在插件数据目录，不写入配置文件 |
| **长度保护** | 字幕超过单次上限时保留开头与结尾，并提示总长与省略比例；用 `offset` 翻页读取任意位置 |
| **字幕缓存** | 同一视频短时间内重复读取直接命中缓存；过期条目顺带清理，超出容量淘汰最久未使用的条目 |
| **明确的失败原因** | 链接无法识别、视频不存在、没有字幕、该分 P 没有字幕、登录失效、风控、网络超时分别给出具体提示 |

---

## 快速开始

### 第一步：安装

MaiBot WebUI → 插件市场 → 搜索 `bilicaption`

### 第二步：扫码登录（必需）

> **提示**：B 站字幕接口需要登录态，未登录无法读取字幕（AI 字幕对匿名用户隐藏）。

1. 打开 MaiBot WebUI → 插件管理 → BiliCaption → 配置页。
2. 点击“获取登录二维码”，用 B 站 App 扫码并在手机上确认。
3. 状态行显示“已登录”即完成。二维码约 170 秒有效，过期后重新获取。

凭据保存在插件数据目录的 `credential.json`；点击“退出登录”会删除该文件。

---

## 配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|:------|:-----|:-------|:-----|
| `view.max_chars` | int | `20000` | `bili_view` 单次返回的字幕最大字符数，`0` 表示不限制 |
| `cache.ttl_seconds` | int | `3600` | 字幕缓存有效期（秒），`0` 关闭缓存 |
| `cache.max_entries` | int | `64` | 字幕缓存最大条数 |

```toml
[plugin]
config_version = "1.2.0"
enabled = true

[view]
max_chars = 20000

[cache]
ttl_seconds = 3600
max_entries = 64
```

---

## LLM 可调用工具

插件注册 1 个 LLM 工具 `bili_view`，模型自行判断何时调用：

```
用户: 总结一下这个视频 https://b23.tv/4bdIZBf
bot → bili_view(video="https://b23.tv/4bdIZBf")
    [字幕] 《人工智能发展简史：从图灵到 GPT》
    （全文 8421 字符，已完整展示）
```

### bili_view

| 参数 | 类型 | 必填 | 说明 |
|:----|:----|:----:|:-----|
| `video` | string | 是 | BV 号、av 号、视频链接、短链或分享文本 |
| `page` | integer | 否 | 分 P 号，从 1 开始；链接已带 `p` 参数或单 P 视频无需传 |
| `offset` | integer | 否 | 从全文第 offset 个字符起连续读取一段；结果提示字幕被省略时用于读取后续内容 |

字幕不超过 `view.max_chars` 时整段返回。超过时返回开头 60% 与结尾 40%，结果中给出省略区间与建议的 `offset`；带 `offset` 调用则返回从该位置起的连续一段，并给出 `next_offset`。

---

## 常见问题

**Q：所有视频都能读到字幕吗？**
A：不是。UP 主未上传字幕且 B 站没有生成 AI 字幕的视频，会返回“没有字幕”的明确提示。

**Q：提示登录失效怎么办？**
A：回到配置页，点击“退出登录”后重新扫码。

**Q：跟 BiliRead 有什么区别？**
A：BiliRead 调用第三方 LLM 总结字幕；本插件直接把字幕交给当前对话的 bot 自行解读。

## 开发

```
pip install -r requirements-dev.txt
ruff format . && ruff check . && pytest
```

## 支持与致谢

如果这个插件对你有帮助，欢迎点亮 Star，有问题和建议请提交 [Issue](https://github.com/OMSociety/maibot_plugin_bilicaption/issues) 或 [Pull Request](https://github.com/OMSociety/maibot_plugin_bilicaption/pulls)。

- [MaiBot](https://github.com/Mai-with-u/MaiBot) 开源聊天机器人框架
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 姊妹插件所在框架
- [SodaCodeSave/astrbot_plugin_biliread](https://github.com/SodaCodeSave/astrbot_plugin_biliread) 上游插件（AGPL-3.0）

## 许可证与作者

本项目采用 **AGPL-3.0** 开源协议（继承上游 BiliRead）。

[@OMSociety](https://github.com/OMSociety)
