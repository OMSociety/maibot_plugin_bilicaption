# Changelog

本项目所有重要更改都会记录在此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.1] - 2026-09-06

### 🐛 修复

- 修复裸短码兜底对任意非链接文本发起网络请求的问题：输入不含 BV 号、不含短链域名时，仅对 5-13 位字母数字的疑似短码走 b23.tv 解析，其余直接判失败（与 AstrBot 版同步）。

### ⚙️ 变更

- 依赖 `bilibili-api-python` 锁定上限版本 `<17.0.0`，避免上游大版本破坏性变更导致插件静默失效。
- 移除不可达的 Cookie 异常分支（行为不变）。
