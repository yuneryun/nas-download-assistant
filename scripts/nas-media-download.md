# NAS 媒体下载栈部署记录（飞牛OS 实测）

> 从 Hermes 主会话迁移的完整过程记录，交接他人时按此部署。

## 环境结论（192.168.1.3 飞牛OS Debian 12）

| 项 | 状态 |
|---|---|
| aria2c | `/usr/bin/aria2c` 现成 |
| ffmpeg/ffprobe | `/usr/bin/ffprobe` 现成 |
| docker | 服务停用（inactive），不可用 → 全用户态方案 |
| sudo | Hermes 账户无免密 sudo → 不装系统服务，用 crontab |
| 磁盘 | /vol2 11T（84% 已用，余 1.8T），媒体放 `/vol2/1000/影视/` |
| 家目录 | `/home/Hermes` 不存在 → 工具写 HOME 会崩，先 `export HOME=/vol1/1000` |

## aria2c 启动（已实测可用）

```bash
setsid aria2c --seed-time=0 --enable-rpc --rpc-listen-port=16800 \
  --rpc-secret=<SECRET> --dir=<下载目录> --file-allocation=none \
  --listen-port=26999 --dht-listen-port=26998 \
  </dev/null >><下载目录>/aria2.log 2>&1 &
```
- 必须 `setsid ... </dev/null &`，`nohup` 经 SSH exec 通道关闭后进程会被杀
- crontab `@reboot` 持久化

## RPC 用法

```bash
# 查活动任务
curl -s http://127.0.0.1:16800/jsonrpc \
  -d '{"jsonrpc":"2.0","id":"1","method":"aria2.tellActive","params":["token:<SECRET>"]}'
# 补 tracker
curl -s http://127.0.0.1:16800/jsonrpc \
  -d '{"jsonrpc":"2.0","id":"1","method":"aria2.changeOption","params":["token:<SECRET>","<GID>",{"bt-tracker":"udp://tracker.opentrackr.org:1337/announce,..."}]}'
```

## Tracker 池（磁力冷启动 0 peer 时补）

```
udp://tracker.opentrackr.org:1337/announce
udp://open.stealth.si:80/announce
udp://tracker.torrent.eu.org:451/announce
udp://open.demonii.com:1337
udp://tracker.dler.org:6969/announce
http://p4p.arenabg.com:1337/announce
udp://exodus.desync.com:6969/announce
udp://tracker.tiny-vps.com:6969/announce
udp://tracker.internetwarriors.net:1337/announce
http://tracker.bt4g.com:2095/announce
```

## 实测案例：盗梦空间 4K REMUX（79.6GB，hash AE3CD89C…）

- 磁力冷启动：元数据 ~2 分钟（DHT），之后 0→39 连接
- 速度爬升：3MB/s(10连接) → 6.6MB/s(41连接) → 23MB/s，BT 马太效应明显
- 老片做种少，新片（半年内）通常快得多
- 一律 `--seed-time=0`（下完不做种，NAS 硬盘省寿命；如需保种改回 0）

## 下载引擎（双引擎支持）

### aria2（默认，skill 内置对接）
- 优势：零依赖单二进制（NAS 固件几乎都自带）、RPC 适合脚本驱动、HTTP/BT 混合
- 脚本 `movie_pipeline.py` 默认走 aria2 RPC

### qBittorrent-nox（可选，WebUI 更友好）
- 优势：DHT/PEX/LSD peer 发现更强、手机浏览器看进度、有完整 WebUI
- 安装（Debian 系）：`sudo apt install qbittorrent-nox` → `qbittorrent-nox` 后台跑（默认 WebUI :8080，首次登录 admin/随机密码看启动日志）
- 与 skill 对接：WebUI 有自己的 API（`/api/v2/`），把 `config.json` 的 `engine` 字段改成 `qbittorrent` 并填 `qb_url/qb_user/qb_pass` 即可；或直接在 WebUI 里手动丢磁力链，下载完仍用 `movie_pipeline.py verify` 走统一校验归档
- 无 sudo 的 NAS：用静态二进制放 `~/bin` 也能跑

### 引擎选择建议
| 场景 | 引擎 |
|---|---|
| AI agent 自动化驱动 | aria2（RPC 简单直接） |
| 手机看进度/手动管理 | qBittorrent（WebUI 体验好） |
| 冷门资源抢连接 | qBittorrent（PEX/LSD 更强） |
| 两者混用 | 完全可以——校验归档层与引擎无关 |

| 标记 | 含义 |
|---|---|
| REMUX | 原盘无损提取（最优先，≈FLAC） |
| BDRip/x265 | 重编码压制（体积小一半，画质略降 ≈ MP3 320k） |
| WEB-DL | 流媒体录制（有"假4K"风险，码率低） |
| DV/HDR10 | 杜比视界/HDR10 动态范围 |
| DTS-HD MA/TrueHD | 无损音轨 |
