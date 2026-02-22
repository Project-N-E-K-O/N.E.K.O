# 简介

**Project N.E.K.O.**（**N**etworked **E**mpathetic **K**nowledging **O**rganism）是一个开源 AI 伙伴平台，将实时语音/文字交互、Live2D/VRM 模型渲染、持久化记忆和基于智能体的任务执行融合为一体化体验。

## 什么是 N.E.K.O.？

N.E.K.O. 是一个面向 AI 伙伴的 UGC（用户生成内容）平台。用户可以创建、自定义和分享拥有独特人设、声音和视觉模型的 AI 角色。系统支持：

- **实时语音对话** —— 通过 WebSocket 连接 Realtime API 提供商（Qwen、OpenAI、Gemini、Step、GLM）
- **Live2D 和 VRM 模型渲染** —— 带有情绪映射动画
- **持久化记忆** —— 跨会话记忆，支持语义召回和时间索引历史
- **后台智能体执行** —— 通过 MCP、Computer Use 和 Browser Use 适配器
- **语音克隆** —— 自定义 TTS 声音
- **Steam 创意工坊集成** —— 内容分享
- **插件系统** —— 开发者扩展

## 适用人群

本文档面向以下**开发者**：

- 参与 N.E.K.O. 核心代码库的贡献
- 构建扩展 N.E.K.O. 功能的插件
- 集成 N.E.K.O. 的 REST 和 WebSocket API
- 在自定义环境中部署 N.E.K.O.
- 了解系统架构以进行调试或扩展

## 快速链接

| 目标 | 从这里开始 |
|------|-----------|
| 搭建开发环境 | [开发环境搭建](./dev-setup) |
| 了解架构 | [架构概览](/zh-CN/architecture/) |
| 构建插件 | [插件快速开始](/zh-CN/plugins/quick-start) |
| 通过 API 集成 | [API 参考](/zh-CN/api/) |
| 使用 Docker 部署 | [Docker 部署](/zh-CN/deployment/docker) |
| 配置系统 | [配置参考](/zh-CN/config/) |

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 实时通信 | WebSocket（原生 + 阿里云 DashScope） |
| 服务间通信 | ZeroMQ（PUB/SUB + PUSH/PULL） |
| LLM 集成 | LangChain + OpenAI 兼容 API |
| TTS | DashScope CosyVoice、GPT-SoVITS |
| 前端 | Vanilla JS、Pixi.js（Live2D）、Three.js（VRM） |
| 记忆存储 | SQLite + 文本嵌入向量 |
| 包管理 | uv（Python 3.11） |
| 容器化 | Docker（多架构） |
