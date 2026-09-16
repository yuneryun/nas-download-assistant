---
name: media-downloader
description: "Use when downloading movies/TV series (4K REMUX/BluRay via magnet on NAS) or music (lossless-first). Search→version selection→download→verify→notify + tv follow-up + watchdog, portable to any machine."
version: 1.1.0
author: Hermes Agent + yuneryun
license: MIT
tags: [download, movie, tv-series, music, nas, aria2, bt, media, watchdog]
---

# 媒体下载器（电影 + 电视剧 + 音乐）统一 Skill

可复用、可交接的媒体下载工作流。给别人用时：只需一台 Linux/Windows 机器（NAS 最佳）+ 本 skill 脚本，改配置文件的几个参数即可。

## 架构总览

```
用户需求(片名/剧名/歌名)
  → ① 搜索: 多站点对比 (TPB/1337x/HAO4K/knaben + musicdl四源)
  → ② 选版: 画质/体积/做种数/预估ETA 对比表 (规则见下)
  → ③ 下载: NAS aria2c 后台 (RPC :16800) / musicdl 本机
  → ④ 校验: ffprobe 四道检查 / mutagen 元数据核对   ← 必过, 不过=未完成
  → ⑤ 归档: 规范命名 + 中文字幕 + 分类入库
  → ⑥ 通知: QQ/其他渠道推送结果
  ⟲ 看门狗: watchdog.py 周期巡检 ③-⑥ 全链路(RPC自愈/卡死救援/水位熔断/自动校验/剧集追更/告警去重)
```

## 配置（scripts/config.json，部署时改这几项）

| 键 | 说明 | 示例 |
|---|---|---|
| `download_dir_movies` | 电影下载目录 | `/vol2/1000/影视/电影` |
| `download_dir_tv` | 电视剧(剧集)目录 | `/vol2/1000/影视/剧集` |
| `download_dir_music` | 音乐目录 | `/vol2/1000/音乐` |
| `aria2_rpc` | aria2 RPC 地址+secret | `http://127.0.0.1:16800` / `fnosdl` |
| `min_free_gb` | 磁盘水位线，低于则拒下并提醒 | `500` |
| `notify_cmd` | 完成通知命令(可选)，`{msg}` 为占位符 | QQ/webhook/echo |

watchdog 子配置（详见下文看门狗节）：`restart_cmd`(RPC掉线自愈命令) / `stall_kbps`+`stall_minutes`(卡死判定) / `auto_verify`(完成自动校验) / `follow_urls`+`auto_add`(剧集追更) / `traffic_subscription`(机场配额告警) / `alert_cooldown_hours`(告警去重窗口, 默认4)。

## 电影流程

### ① 搜索选版（核心规则）
多站点搜索（TPB `tpb.party`、1337x、HAO4K 等），每个版本提取：**分辨率/编码/体积/做种数/磁力链**，输出对比表。用户已定死的规则：
- **画质和文件大小优先，可牺牲速度**（4K REMUX ≈ 音乐里的 FLAC）
- 做种数 **≥30** 的最高画质版 → 直接下
- 预估 ETA **<24h** → 不换版本；**>24h** → 才降级（4K 压制 x265 → 1080p REMUX）
- 推荐标注格式：`⭐画质最佳` / `⚡最快` / `⚖平衡`

### ② 下载前：流量配额闸门（必查）
BT/aria2 直连下载不占机场，但仍要在启动前查出口机场余量（防代理链路被规则意外接管）：
```bash
curl -sI -A "clash-verge-rev" "<订阅URL>" | grep -i subscription-userinfo
# download=已用 total=总额 → 剩余 = total-download
```
- 文件大小 ≤ 剩余流量的 30% 才允许直下，否则**先报数给用户请示**，绝不默默开下
- 本环境实况（已查实）：NAS aria2 启动命令**无 proxy 参数 + mihomo TUN 关闭** → 电影 BT 数据不走机场，只有搜索 knaben 那几十 KB 走；机场烧流量的真正来源通常是**电脑侧大文件下载经系统代理 7897 出境**（如模型/数据集 GB 级下载时 Clash 规则未放行直连）——查机场后台按天明细定位，别误判给 NAS 下载。用户主力=clashgit.com(250GB档)，备用=一元机场(旧节点已失效待重导)
- 下载完成后把消耗量记进结果汇报

### ③ 下载（aria2c，NAS 后台）
- NAS mihomo 不是常驻保证：开机后需确认 7890 在听（曾出现 9/9 关停后一直没人拉起）
```bash
aria2c --enable-rpc --rpc-listen-port=16800 --rpc-secret=<secret> \
  --dir=<download_dir_movies> --file-allocation=none \
  --seed-time=0 --listen-port=26999 --dht-listen-port=26998 \
  --bt-tracker="< trackers 列表,逗号分隔>" "<magnet>"
```
- 磁力冷启动 0 peer 时：先等 DHT（1-2 分钟），再补 tracker（见 `scripts/nas-media-download.md` 的 tracker 池），仍不行换同内容其他 hash 的种子
- 下载中每 30 分钟查一次速度（RPC `aria2.tellActive`），掉到 1MB/s 以下且 ETA>24h → 提醒用户换版
- **磁盘水位检查**：开下之前 `df` 确认剩余 > min_free_gb

### ④ 完整性校验（必过四道关卡，下载≠完成）
```bash
# 1. 元数据: 分辨率/码率/时长
ffprobe -v error -select_streams v:0 -show_entries \
  stream=width,height,bit_rate,codec_name -show_entries format=duration,size <file>
# 2. 解码实测: 抽开头/中间/结尾各30秒, 零error才算过
ffmpeg -v error -ss <pos> -i <file> -t 30 -f null - 2>&1
# 3. BT哈希: aria2 下载完成即自动校验 (check-integrity)
# 4. 容器: ffprobe 无 "Invalid data" 报错
```
任何一项失败 → 重试校验 → 仍失败则删除残件并报告，**绝不留看不了的文件在库里**。
REMUX 预期：3840×2160 / 视频码率 40-80Mbps / HEVC / HDR10 或 DV 元数据。

### ⑤ 归档（三件套）
1. **改名**：`盗梦空间 (2010) Inception.mkv`（中文+年份+英文，刮削器友好；电视剧 `剧名 S01E01.mkv`）
2. **字幕**：从 OpenSubtitles/SubHD 搜简体 SRT，放同目录同名；搜不到则在结果里注明"无字幕"
3. **入库**：按 `电影/电视剧/纪录片/动漫` 分类移动，写入库清单（`media_library.json`：片名/路径/体积/校验日期）——这也是**重复下载防护**的索引：下载前先查清单

### ⑥ 通知
调用 config 的 `notify_cmd`，报告：片名/体积/校验结果/字幕有无/存放路径。

## 电视剧流程（tv_pipeline.py）

与电影共用搜索/选版/下载/校验底层，特殊在**多集批量 + 追更 + 逐集账本**。命令：`pick/add/progress/verify/scan/follow`。

### ① 搜索选版（电视剧特化规则）
- 搜索词：`<剧名/英文名> S01 1080p x265` / `Season 1 pack WEB-DL`；站源同电影（knaben 需代理，见 nas-media-download.md）
- **画质标准与电影不同**：剧集 1080p WEB-DL/x265 即达标（流媒体原盘就是这画质，REMUX 无意义）；神剧/HBO 旗舰剧才上 4K
- **整季包(pack) vs 分集(split)**：整季包做种≥30 且 ETA≤48h → 一锅端；否则分集下（单集 2-6GB，做种通常比大包多）。`pick versions.json` 自动出建议
- 选版数据格式：`[{name,size_gb,seeders,kind:pack|split,quality_rank,episodes}]`，quality_rank: -1=4K/0=1080p达标/1=720p降级

### ② 下载
- `add <磁力> "剧名 S01" [--ep 1-12]`：整包磁力等元数据就绪后按 `select-file` 只挑想要的集（省盘省流）；分集磁力逐条 add
- 任务登记进 `.tv_progress.json` 清单（gid→集号→文件），断链后凭清单里的 magnet 可重 add 续传

### ③ 逐集校验（四道，同电影标准但预期不同）
- 预期：1920×1080（或 4K）/ HEVC 或 x264 / 时长 20-60min（美剧）或 40-50min（国产剧）
- **电视剧独有：时长离群检测** —— 与本季已入库集中位数比，>2.2倍=误下合集/电影版，<0.45倍=删减/预告片，直接判不合格
- 残件**移入 `_quarantine/` 隔离区等用户处置，绝不删除**（用户铁律：任何文件不代删）

### ④ 归档
- 目录结构：`剧集/剧名 S01/剧名 S01E01.mkv`（Emby/Jellyfin/Plex 通吃）
- 库索引 `media_library.json` 逐集记录 show/season/episode/时长/体积 —— 既是查重依据也是时长基准
- 字幕：逐集外挂 SRT 同名同目录（OpenSubtitles 整季一次搜齐）

### ⑤ 盘点与追更
- `scan 剧名 --season S01 --expect 12` → 缺集列表（追更/新季开播先用它）
- `follow 剧名 <发布页URL> [--auto]` → 提磁力→过滤已知集→只报新集；`--auto` 直接入队（watchdog 周期跑这个 = 全自动追更）

## 看门狗（watchdog.py）

crontab 周期拉起的一轮巡检，六项职责（`python watchdog.py install` 打印 crontab 行）：
1. **RPC 保活自愈**：aria2 不在听 → 执行 `watchdog.restart_cmd` 拉起再验；拉不起才告警（NAS 重启后下载栈没人管是实战真坑）
2. **卡死救援**：速度<`stall_kbps`(默认50) 持续 `stall_minutes`(默认30) → 补 tracker 池 → 仍卡 → remove+add 原磁力重连一次 → 仍卡才告警换版。同一任务不反复横跳
3. **完成钩子+待校验队列**：任务从 active 消失且目标文件在盘 → 写入 `verify_queue.json` → 下一轮自动调 movie/tv pipeline 校验归档（`auto_verify:false` 可关，改为只通知）
4. **磁盘水位熔断**：三个媒体目录余量 < min_free_gb → `aria2.pause` 全部任务+告警（暂停不是删除）
5. **剧集追更**：扫 `follow_urls` 各页面，新集入队/报告
6. **流量配额告警**：配了 `traffic_subscription` 时每轮查 `subscription-userinfo` 头，剩余流量<未下完体积的30% → 告警

**设计约束**：告警去重（同 key 默认 4h 只报一次，防刷屏）；状态存 `.watchdog_state.json`；一切动作可审计（`watchdog.log`）；**绝无删除文件代码路径**。`status` 子命令看现场，`test-notify` 测通知链路。

## 音乐流程

沿用 musicdl（Python 包，Windows `.venv`/Linux pip 均可），完整坑与校验逻辑见 `references/musicdl-core.md`（从原 musicdl-music-download skill 迁移）。要点：
- **无损优先**：FLAC/WAV/APE > 320k MP3
- **「下A得B」必防**：搜索结果歌名不可信，下载后必须 mutagen 读元数据核对歌名+歌手
- **翻唱过滤**：按 singers 字段过滤 + 坏词黑名单（Remix/Cover/Live/伴奏…）
- **断点续跑**：目标文件已存在则 SKIP，manifest 记录进度
- **音质保底规则（用户定死）：保证能下到**。无损没有就逐级降：FLAC > 320k MP3 > 192k > 任意可听版本；绝不因无无损而放弃，降级后在交付清单里标注实际音质
- 下载后用 mutagen 确认真无损（16bit/44.1kHz 以上）

## 部署边界（设计原则：核心层 vs 环境层）

本 skill **只管核心层**，环境层由用户自行解决：

| 层 | 内容 | 归属 |
|---|---|---|
| ✅ 核心层（skill 负责） | 搜索选版/下载触发/四道校验/选版规则/归档命名逻辑 | 本 skill 的文档+脚本 |
| ❌ 环境层（用户自备） | 下载引擎安装与启动、目录创建、权限设置、进程自启、通知通道 | 各 NAS 固件自己的文档 |

**原因**：NAS 固件的权限层（飞牛 TrimACL/群晖 synoacltool/威联通 QACL）差异大且只认 root，适配它们会让 skill 膨胀成固件手册且永远测不全。目录/引擎/权限是每台机器一次性工作，用户自己做反而快。

skill 内置的 `selftest.py` 会在环境没准备好时明确报缺什么（RPC 不通/ffprobe 缺失/目录不可写），照着修即可。权限相关的实战坑（飞牛双层 ACL 等）保留在 FAQ 里作参考。

## 已知环境坑（飞牛OS实测，供参照）
Hermes 账户家目录 `/home/Hermes` 不存在 → 一切写 HOME 的工具（rclone/musicdl/aria2 vfs cache）先 `export HOME=<可写目录>`
- 无 sudo / docker daemon 停用 → 全部用户态方案（aria2c 二进制 + setsid + crontab）
- `nohup` 经 SSH exec 会随通道死 → 必须 `setsid ... </dev/null &`
- SSH 公钥认证失败（sshd StrictModes）→ paramiko 密码登录

## 参考文件
- `scripts/nas-media-download.md` — NAS 下载栈完整部署记录（tracker 池、RPC 用法、飞牛坑）
- `scripts/movie_pipeline.py` — 搜索→选版→下载→校验→归档 一体化脚本
- `scripts/tv_pipeline.py` — 电视剧：pick/add(按集选文件)/progress/verify(时长离群检测)/scan(缺集盘点)/follow(追更)
- `scripts/watchdog.py` — 看门狗：run(crontab)/status/install/test-notify 六项巡检
- `scripts/verify_media.py` — 音乐完整性校验器（五道关卡，输出 verify_report.json）
- `scripts/selftest.py` — 部署自检
- `references/musicdl-core.md` — 音乐下载完整坑库
- `references/music-optimization.md` — 音乐进阶优化（Hi-Res验证/并发提速/元数据补全/查重）
- `references/optimization-playbook.md` — 找不到资源/下载慢的优化手册（进阶）
- `references/faq-troubleshooting.md` — 他人部署常见问题排查手册（拉取失败/依赖/配置/安全）
- `references/ai-handoff.md` — AI-to-AI 交接指南（发给别的 NAS 上的 agent 时让对方先读这份）
