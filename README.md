<div align="center">

<img src="https://raw.githubusercontent.com/OMSociety/maibot_plugin_bilicaption/main/logo.png" width="120" alt="BiliCaption Logo" />

# 🎬 BiliCaption B站字幕提取解读

**B 站视频字幕提取与深度解读助手** —— 字幕纯文本提取 · 完整字幕通读 · 链接自动识别 · 长度可控

[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](https://github.com/OMSociety/maibot_plugin_bilicaption)
[![MaiBot](https://img.shields.io/badge/MaiBot-%E2%89%A51.0-green.svg)](https://github.com/Mai-with-u/MaiBot)
[![License](https://img.shields.io/badge/license-AGPL--3.0-orange.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/OMSociety/maibot_plugin_bilicaption)](https://github.com/OMSociety/maibot_plugin_bilicaption/stargazers)
[![Issues](https://img.shields.io/github/issues/OMSociety/maibot_plugin_bilicaption)](https://github.com/OMSociety/maibot_plugin_bilicaption/issues)

[✨ 核心特性](#-核心特性) • [📖 功能概览](#-功能概览) • [🚀 快速开始](#-快速开始) • [⚙️ 配置项说明](#️-配置项说明) • [🛠️ LLM 可调用工具](#️-llm-可调用工具) • [⚠️ 常见问题](#️-常见问题) • [📝 更新日志](CHANGELOG.md)

</div>

> 🎨 本项目由 AI 编写 · 由 AstrBot 插件 [OMSociety/astrbot_plugin_bilicaption](https://github.com/OMSociety/astrbot_plugin_bilicaption) 迁移而来 · 源码基于 [SodaCodeSave/astrbot_plugin_biliread](https://github.com/SodaCodeSave/astrbot_plugin_biliread) 二次开发

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 📝 **字幕纯文本提取** | 提取 B 站视频字幕原文，不做 AI 总结，直接返回给用户 |
| 🧠 **深度字幕通读** | 完整字幕喂给 bot 自身，由 bot 自行解读 / 总结视频内容（可选，高 token 消耗） |
| 🔗 **链接智能识别** | 支持 B 站完整链接 / BV 号 / b23.tv 短链 / 裸短码，自动识别解析 |
| ✂️ **长度控制** | 两个工具可分别配置字幕最大返回长度，防止上下文溢出 |
| 🔒 **登录态支持** | 配置 B 站 Cookie 后获取完整 AI 字幕（字幕接口需要登录态） |

---

## 📖 功能概览

### 字幕提取 bilibili_caption
聊天中直接发 B 站链接 / BV 号，bot 自动调用工具返回字幕纯文本。

### 深度解读 bilibili_read
开启后，要求 bot 解读视频时自动通读完整字幕再组织语言。

### 两个工具的区别

| 特性 | `bilibili_caption` | `bilibili_read` |
|:----|:-------------------|:----------------|
| 定位 | 快速提取字幕原文 | 为 bot 自身阅读做深度解读 |
| 返回 | 字幕文本，供 AI 展示给用户 | 完整字幕，喂入 bot 思考流 |
| token 消耗 | 可控（可截断） | 较高（默认全文通读） |
| 是否默认启用 | ✅ 始终可用 | ❌ 默认关闭，需开启配置 |

---

## 🚀 快速开始

### 第一步：安装

**方式一：插件市场**
- MaiBot WebUI → 插件市场 → 搜索 `bilicaption`

**方式二：手动安装**
- 克隆仓库到 MaiBot 的 `plugins/` 目录：

```bash
git clone https://github.com/OMSociety/maibot_plugin_bilicaption.git plugins/maibot_plugin_bilicaption
```

### 第二步：配置 B 站 Cookie（必需）

> 💡 B 站字幕接口需要登录态，**不配置 Cookie 无法获取字幕**（AI 字幕对匿名用户隐藏）。请先配置再使用。

在插件配置的 `bilibili_cookie` 分组中填写：

| 字段 | 获取方式 |
|:----|:----|
| `sessdata` | 浏览器登录 [bilibili.com](https://www.bilibili.com) → F12 → Application → Cookies → 复制 `SESSDATA` 的值 |
| `bili_jct` | 同上，复制 `bili_jct` 的值 |

### 第三步：重启生效

配置完成后在 WebUI 重载插件（或重启 MaiBot），即可在对话中发送 B 站链接测试。

---

## ⚙️ 配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|:------|:-----|:-------|:-----|
| `bilibili_cookie.sessdata` | string | `""` | B 站 SESSDATA Cookie（必需，字幕接口需要登录态） |
| `bilibili_cookie.bili_jct` | string | `""` | B 站 bili_jct Cookie |
| `read_settings.max_subtitle_length` | int | `0` | caption 工具字幕最大字符数，`0` 表示不限制 |
| `read_settings.enable_read_tool` | bool | `false` | 启用 `bilibili_read` 深度解读工具（高 token 消耗） |
| `read_settings.read_max_subtitle_length` | int | `0` | read 工具字幕最大字符数，`0` 表示不限制（全文通读） |

### 快速配置模板

在 MaiBot WebUI 插件配置面板填写，或参考以下 `config.toml` 结构（插件目录下，首次加载自动生成）：

```toml
[plugin]
config_version = "1.0.0"
enabled = true

[bilibili_cookie]
sessdata = "你的SESSDATA"
bili_jct = "你的bili_jct"

[read_settings]
max_subtitle_length = 0
enable_read_tool = false
read_max_subtitle_length = 0
```

---

## 🛠️ LLM 可调用工具

插件注册 2 个 LLM 工具（`bilibili_read` 需开启配置 `enable_read_tool`），模型会自动判断何时调用，你只需用自然语言说需求：

```
用户: 帮我提取这个视频的字幕 https://b23.tv/4bdIZBf
🤖 → bilibili_caption(bvid=https://b23.tv/4bdIZBf)
    [字幕] 《人工智能发展简史：从图灵到 GPT》
    大家好，欢迎来到本期视频...

用户: 解读一下这个视频 BV1GJ411x7h7
🤖 → bilibili_read(bvid=BV1GJ411x7h7)
    （通读完整字幕后自行组织语言输出解读）
```

### bilibili_caption
获取哔哩哔哩视频的字幕纯文本。如果视频没有字幕则返回提示信息。

| 参数 | 类型 | 必填 | 说明 |
|:----|:----|:----:|:-----|
| `bvid` | string | ✅ | BVID / B 站完整链接 / b23.tv 短链，例如 `BV1GJ411x7h7` 或 `https://b23.tv/4bdIZBf` |

### bilibili_read（需开启配置 `enable_read_tool`）
通读哔哩哔哩视频的完整字幕以便 bot 解读视频内容。当用户要求总结、分析、评价某个 B 站视频时调用。

| 参数 | 类型 | 必填 | 说明 |
|:----|:----|:----:|:-----|
| `bvid` | string | ✅ | BVID / B 站完整链接 / b23.tv 短链 |

> 注意：`bilibili_read` 返回完整字幕原文，不附加任何预制提示词。由 bot 自行阅读后决定如何解读。

---

## ⚠️ 常见问题

**Q：需要配置吗？**
A：**需要**。B 站字幕接口要求登录态，请配置 `bilibili_cookie.sessdata` 与 `bili_jct`（获取方式见[快速开始](#-快速开始)）。

**Q：所有视频都能获取字幕吗？**
A：不是。UP 主未上传字幕且 B 站无 AI 字幕的视频无法获取内容，此时会提示「暂无可用字幕」。

**Q：`bilibili_caption` 和 `bilibili_read` 有什么区别？**
A：caption 快速返回字幕文本让你看；read 把全文喂给 bot 让 bot 自己通读再输出解读。read 费 token 但解读质量更高。两者互不替代，可按需配置开关。

**Q：跟 BiliRead 有什么区别？**
A：BiliRead 调用第三方 LLM 总结字幕；本插件跳过第三方 LLM，直接返回字幕原文，或利用当前对话的 bot 自身做解读。

---

## ⭐ 支持本项目

如果这个插件对你有帮助，欢迎点亮 Star ⭐，有问题和建议请提交 [Issue](https://github.com/OMSociety/maibot_plugin_bilicaption/issues) 或 [Pull Request](https://github.com/OMSociety/maibot_plugin_bilicaption/pulls)。

## 🙏 致谢

- [MaiBot](https://github.com/Mai-with-u/MaiBot) 开源聊天机器人框架
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 上游 AstrBot 插件框架
- [SodaCodeSave/astrbot_plugin_biliread](https://github.com/SodaCodeSave/astrbot_plugin_biliread) 上游插件（AGPL-3.0）

---

## 📜 许可证

本项目采用 **AGPL-3.0** 开源协议（继承上游 BiliRead）。

---

## 👤 作者

[@OMSociety](https://github.com/OMSociety)
