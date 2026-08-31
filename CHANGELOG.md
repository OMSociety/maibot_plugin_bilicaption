# Changelog

## [1.0.0] - 2026-08-27

### ✨ 新功能

- 从 AstrBot 插件 [astrbot_plugin_bilicaption](https://github.com/OMSociety/astrbot_plugin_bilicaption) 迁移为 MaiBot 原生插件（maibot-plugin-sdk 2.x）
- 注册 2 个 LLM 工具：`bilibili_caption`（字幕纯文本提取）、`bilibili_read`（完整字幕通读，默认关闭）
- 支持 B 站完整链接 / BV 号 / b23.tv 短链 / 裸短码自动识别
- 长度控制：两个工具可分别配置字幕最大返回长度

### ⚠️ 行为变化

- 配置系统从 `_conf_schema.json` 迁移为 MaiBot `config_model`（`config.toml` 存储，WebUI 自动渲染）
- `auto_send_txt` 开关保留但**暂无效**：MaiBot 插件 SDK 暂不支持文件消息推送，字幕以文本返回
- `bilibili_read` 未启用时在加载时自动禁用该组件（不出现在 LLM 工具列表）

### 🧪 测试

- 迁移 `test_subtitle_utils.py` 纯逻辑测试（不依赖网络与框架环境）
