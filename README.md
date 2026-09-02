# NAS 下载助手 (nas-download-assistant)

电影 + 音乐统一媒体下载 skill。为 Hermes Agent 等 AI agent 设计，也可独立使用脚本。

## 功能
- 🎬 **电影**：多站点搜索 → 版本对比表（画质/体积/做种/ETA）→ 磁力下载（aria2）→ **四道完整性校验** → 规范命名归档 → 中文字幕
- 🎵 **音乐**：无损优先（FLAC/WAV）→ 翻唱过滤 → 「下A得B」元数据校验 → 断点续跑

## 用户策略（作者实测沉淀）
- 画质和文件大小优先，可牺牲下载速度
- 做种数 ≥30 的最高画质版直接下
- 预计 ETA <24h 不换版本；>24h 才降级
- 下载完成 ≠ 任务完成：ffprobe 元数据/解码实测/BT哈希/容器 四道校验全过才算数

## 部署
```bash
# 依赖 (Linux NAS)
sudo apt install aria2 ffmpeg python3-pip   # 或用户态二进制
pip install musicdl mutagen
# 改 scripts/config.json 里的目录/RPC/通知
python scripts/selftest.py   # 自检
```
Windows 参见 SKILL.md「部署到新机器」。

## 结构
```
SKILL.md                     # 完整工作流文档 (给 AI agent 读)
scripts/movie_pipeline.py    # search/download/status/verify 命令
scripts/selftest.py          # 部署自检
scripts/nas-media-download.md# NAS(飞牛OS)部署实录+tracker池
references/musicdl-core.md   # 音乐下载坑库
```

## 来源
作者在 Hermes Agent + 飞牛OS NAS 上实战迭代，已验证：盗梦空间 4K REMUX (79.6GB) 完整下载+校验流程。
