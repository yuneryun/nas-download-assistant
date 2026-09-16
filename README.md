# NAS 下载助手 (nas-download-assistant)

电影 + 电视剧 + 音乐统一媒体下载 skill，含下载栈看门狗。为 Hermes Agent 等 AI agent 设计，也可独立使用脚本。

## 功能
- 🎬 **电影**：多站点搜索 → 版本对比表（画质/体积/做种/ETA）→ 磁力下载（aria2）→ **四道完整性校验** → 规范命名归档 → 中文字幕
- 📺 **电视剧**：整季包/分集智能选版 → 按集选文件下载 → 逐集校验（含**本季时长离群检测**，抓错下合集/预告片）→ `SxxEyy` 规范归档 → 缺集盘点 → **追更**（盯发布页自动发现新集）
- 🎵 **音乐**：无损优先（FLAC/WAV）→ 翻唱过滤 → 「下A得B」元数据校验 → 断点续跑
- 🐕 **看门狗**：crontab 周期巡检——aria2 掉线自愈、0速卡死补tracker/重连、下载完成自动进校验队列、磁盘低水位熔断暂停、剧集追更扫描、机场配额告警；告警去重防刷屏，**代码中无任何删除文件路径**（残件只移隔离区）

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
# 改 scripts/config.json 里的目录/RPC/通知/watchdog
python scripts/selftest.py   # 自检
python scripts/watchdog.py install   # 打印 crontab 行(看门狗, 强烈建议装)
```
Windows 参见 SKILL.md「部署到新机器」。

## 结构
```
SKILL.md                     # 完整工作流文档 (给 AI agent 读)
scripts/movie_pipeline.py    # 电影: search/download/status/verify
scripts/tv_pipeline.py       # 电视剧: pick/add/progress/verify/scan/follow
scripts/watchdog.py          # 看门狗: run(crontab)/status/install/test-notify
scripts/selftest.py          # 部署自检
scripts/nas-media-download.md# NAS(飞牛OS)部署实录+tracker池
references/musicdl-core.md   # 音乐下载坑库
```

## 来源
作者在 Hermes Agent + 飞牛OS NAS 上实战迭代。

## ✅ 实战验证记录

### 案例：盗梦空间 Inception (2010) 4K REMUX — 2026-09-02

| 项 | 数值 |
|---|---|
| 版本 | Inception.2010.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-FGT |
| 体积 | 79.57 GiB（85,436,578,297 字节，与源逐字节一致） |
| 下载耗时 | 70 分钟（速度 3→23 MB/s 爬升，BT 马太效应） |
| 元数据 | 3840×2160 HEVC 10bit，时长 148.1 min（8888s，与实际片长一致） |
| 音轨 | DTS-HD MA 主音轨 + 多条 AC3 评论音轨，完整保留 |
| 解码实测 | 开头/中段/结尾抽样解码零错误，中段实测播放推进正常 |
| BT 哈希 | aria2 自动校验通过（Download complete） |
| 结论 | ✅ 四道校验全过，可正常观看 |

> 中段 ffmpeg null-muxer 输出的 "non monotonically increasing dts" 为 null 封装的正常提示，非文件损坏；以实际解码推进（time 正常走秒）为准。

### 经验
- BT 速度呈马太效应：起手 3MB/s → 40+ 连接后 20MB/s+，评估 ETA 要留余量
- REMUX 无封装中文字幕（原盘特性），需外挂 SRT

