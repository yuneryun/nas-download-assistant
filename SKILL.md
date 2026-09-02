---
name: media-downloader
description: "Use when downloading movies (4K REMUX/BluRay via magnet on NAS) or music (lossless-first). Search→version selection→download→verify→notify, portable to any machine."
version: 1.0.0
author: Hermes Agent + yuneryun
license: MIT
tags: [download, movie, music, nas, aria2, bt, media]
---

# 媒体下载器（电影 + 音乐）统一 Skill

可复用、可交接的媒体下载工作流。给别人用时：只需一台 Linux/Windows 机器（NAS 最佳）+ 本 skill 两个参考脚本，改配置文件的几个参数即可。

## 架构总览

```
用户需求(片名/歌名)
  → ① 搜索: 多站点对比 (TPB/1337x/HAO4K + musicdl四源)
  → ② 选版: 画质/体积/做种数/预估ETA 对比表 (规则见下)
  → ③ 下载: NAS aria2c 后台 (RPC :16800) / musicdl 本机
  → ④ 校验: ffprobe 四道检查 / mutagen 元数据核对   ← 必过, 不过=未完成
  → ⑤ 归档: 规范命名 + 中文字幕 + 分类入库
  → ⑥ 通知: QQ/其他渠道推送结果
```

## 配置（scripts/config.json，部署时改这几项）

| 键 | 说明 | 示例 |
|---|---|---|
| `download_dir_movies` | 电影下载目录 | `/vol2/1000/影视/电影` |
| `download_dir_music` | 音乐目录 | `/vol2/1000/音乐` |
| `aria2_rpc` | aria2 RPC 地址+secret | `http://127.0.0.1:16800` / `fnosdl` |
| `min_free_gb` | 磁盘水位线，低于则拒下并提醒 | `500` |
| `notify_cmd` | 完成通知命令(可选) | QQ/webhook/echo |

## 电影流程

### ① 搜索选版（核心规则）
多站点搜索（TPB `tpb.party`、1337x、HAO4K 等），每个版本提取：**分辨率/编码/体积/做种数/磁力链**，输出对比表。用户已定死的规则：
- **画质和文件大小优先，可牺牲速度**（4K REMUX ≈ 音乐里的 FLAC）
- 做种数 **≥30** 的最高画质版 → 直接下
- 预估 ETA **<24h** → 不换版本；**>24h** → 才降级（4K 压制 x265 → 1080p REMUX）
- 推荐标注格式：`⭐画质最佳` / `⚡最快` / `⚖平衡`

### ② 下载（aria2c，NAS 后台）
```bash
aria2c --enable-rpc --rpc-listen-port=16800 --rpc-secret=<secret> \
  --dir=<download_dir_movies> --file-allocation=none \
  --seed-time=0 --listen-port=26999 --dht-listen-port=26998 \
  --bt-tracker="< trackers 列表,逗号分隔>" "<magnet>"
```
- 磁力冷启动 0 peer 时：先等 DHT（1-2 分钟），再补 tracker（见 `scripts/nas-media-download.md` 的 tracker 池），仍不行换同内容其他 hash 的种子
- 下载中每 30 分钟查一次速度（RPC `aria2.tellActive`），掉到 1MB/s 以下且 ETA>24h → 提醒用户换版
- **磁盘水位检查**：开下之前 `df` 确认剩余 > min_free_gb

### ③ 完整性校验（必过四道关卡，下载≠完成）
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

### ④ 归档（三件套）
1. **改名**：`盗梦空间 (2010) Inception.mkv`（中文+年份+英文，刮削器友好；电视剧 `剧名 S01E01.mkv`）
2. **字幕**：从 OpenSubtitles/SubHD 搜简体 SRT，放同目录同名；搜不到则在结果里注明"无字幕"
3. **入库**：按 `电影/电视剧/纪录片/动漫` 分类移动，写入库清单（`media_library.json`：片名/路径/体积/校验日期）——这也是**重复下载防护**的索引：下载前先查清单

### ⑤ 通知
调用 config 的 `notify_cmd`，报告：片名/体积/校验结果/字幕有无/存放路径。

## 音乐流程

沿用 musicdl（Python 包，Windows `.venv`/Linux pip 均可），完整坑与校验逻辑见 `references/musicdl-core.md`（从原 musicdl-music-download skill 迁移）。要点：
- **无损优先**：FLAC/WAV/APE > 320k MP3
- **「下A得B」必防**：搜索结果歌名不可信，下载后必须 mutagen 读元数据核对歌名+歌手
- **翻唱过滤**：按 singers 字段过滤 + 坏词黑名单（Remix/Cover/Live/伴奏…）
- **断点续跑**：目标文件已存在则 SKIP，manifest 记录进度
- 下载后用 mutagen 确认真无损（16bit/44.1kHz 以上）

## 部署到新机器（交接给别人）

1. 把本 skill 目录整个拷过去（含 `scripts/`）
2. Linux NAS：装 `aria2 ffmpeg python3-pip` → `pip install musicdl mutagen` → 改 `config.json` → 起 aria2c（crontab `@reboot` 自启）
3. Windows：装 ffmpeg + qBittorrent 或 aria2，其余同
4. 跑 `scripts/selftest.py` 自检（连 RPC、试下小文件、ffprobe 版本）

## 已知环境坑（飞牛OS实测）

- Hermes 账户家目录 `/home/Hermes` 不存在 → 一切写 HOME 的工具（rclone/musicdl/aria2 vfs cache）先 `export HOME=<可写目录>`
- 无 sudo / docker daemon 停用 → 全部用户态方案（aria2c 二进制 + setsid + crontab）
- `nohup` 经 SSH exec 会随通道死 → 必须 `setsid ... </dev/null &`
- SSH 公钥认证失败（sshd StrictModes）→ paramiko 密码登录

## 参考文件
- `scripts/nas-media-download.md` — NAS 下载栈完整部署记录（tracker 池、RPC 用法、飞牛坑）
- `scripts/movie_pipeline.py` — 搜索→选版→下载→校验→归档 一体化脚本
- `scripts/verify_media.py` — 音乐完整性校验器（五道关卡，输出 verify_report.json）
- `scripts/selftest.py` — 部署自检
- `references/musicdl-core.md` — 音乐下载完整坑库
- `references/music-optimization.md` — 音乐进阶优化（Hi-Res验证/并发提速/元数据补全/查重）
- `references/optimization-playbook.md` — 找不到资源/下载慢的优化手册（进阶）
