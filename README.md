# NAS 下载助手 (nas-download-assistant)

电影 + 电视剧 + 音乐 统一媒体下载 skill，**双下载通道（BT 磁力 / 网盘分享链）**，配下载栈看门狗。为 Hermes Agent 等 AI agent 设计，脚本也可独立使用（Linux / Windows / 飞牛OS、群晖、威联通等任意 NAS，核心零第三方依赖，只用 Python 标准库 + aria2 + ffmpeg）。

> ⚖️ **本项目仅供个人学习、研究与技术交流，请支持正版。使用前必读文末[免责声明](#-免责声明)。**

## 它解决什么问题

用 BT/磁力在 NAS 上下载影音，难的不是"点下载"，而是这些工程问题，本项目把每个都做成闭环：

| 痛点 | 本项目的答案 |
|---|---|
| 同一部片几十个版本，选哪个？ | 选版引擎：画质/体积/做种数/ETA 对比表 + 作者实测规则（画质优先可牺牲速度，做种≥30直下，ETA>24h才降级） |
| BT 挂半天没做种 / 新片找不到源？ | **网盘通道**：PanSou 聚合搜链（90+TG频道+60+资源站）→ 公开 API 秒验死链+列真实体积 → 转存 → OpenList 出直链 → 同一个 aria2 多线程入库。**无网盘会员可用** |
| 磁力挂上去 0 速度干等？ | 看门狗卡死救援：补 tracker → 重连任务 → 仍救不活才告警让你换版，全程留痕 |
| "下完了"其实看不了？ | **四道硬校验**才算完成：ffprobe 元数据 → 开中尾三段解码实测 → BT哈希 → 容器检查；下载完成 ≠ 任务完成 |
| 电视剧一季 10 集怎么管？ | 整季包/分集自动选版、`--ep 1-12` 按集选文件省盘、逐集校验归档 `SxxEyy`、缺集盘点、**盯发布页自动追更** |
| 下A得B / 假无损 / 假4K？ | 音乐 mutagen 元数据核对+真无损验证；视频码率下限检测；剧集本季时长离群检测（抓错下合集/预告片） |
| NAS 重启下载栈没人管？ | 看门狗 RPC 保活自愈（crontab 一行）；磁盘低水位自动熔断暂停；机场流量配额告警 |
| 下完 Emby/Jellyfin 要手动扫库？ | 归档成功自动触发媒体库增量刷新（未部署则静默跳过） |
| 归档乱、重复下载？ | 规范命名（中文+年份+英文，刮削器友好）+ `media_library.json` 入库清单查重 |

**安全设计**：所有脚本与看门狗**没有任何删除文件的代码路径**——校验失败残件只移入 `_quarantine/` 隔离区、磁盘不足只暂停任务，处置权永远在人手里。

## 功能矩阵

- 🎬 **电影**：多站点搜索（TPB/1337x/HAO4K/knaben）→ 版本对比表 → aria2 磁力下载 → 四道校验 → 规范归档 → 中文字幕指引
- ☁️ **网盘通道**（`pan_pipeline.py`，2026-09 实测定：无会员条件下夸克/UC 是唯一可自动化的国内网盘）：
  - `search`：PanSou 聚合一次搜出 夸克/UC/阿里/百度/115/迅雷/磁力 全类型分享链（免鉴权）
  - `check`：夸克公开 API 验链 + **递归展开目录** → 真实文件列表与体积（免登录），自动标记 >40GB 单文件"回 BT 通道"
  - `save`：转存到网盘根目录（`--keep` 只挑命中子集省每日配额；异步 task 轮询 + 读回目录确认落盘）
  - `fetch`：OpenList `fs/get` 取 `raw_url` 直链 → **复用同一套 aria2 RPC** 多线程入库，与 BT 任务一起被看门狗盯
  - 分流规则：4K REMUX/单文件>40GB → BT；WEB-DL/剧集/新片 → 网盘优先。百度/115 无会员不可自动化（115 开放平台 2026-08-09 已停服），详见 `references/pan-quark-channel.md`
- 📺 **电视剧**（`tv_pipeline.py`）：
  - `pick`：整季包 vs 分集智能建议（剧集 1080p WEB-DL 即达标，神剧才上 4K；包 ETA>48h 降级分集）
  - `add --ep 1-12`：磁力包等元数据就绪后按集 select-file，只下要的集
  - `verify`：逐集四道校验 + 本季时长离群检测；归档 `剧名 S01/剧名 S01E01.mkv`（Emby/Jellyfin/Plex 通吃）
  - `scan --expect 12`：缺集盘点　`follow <发布页URL> --auto`：新集自动发现入队
- 🎵 **音乐**（musicdl 四源）：无损优先（FLAC>320k>192k>任意可听，保证能下到）、翻唱过滤、「下A得B」元数据必防、断点续跑
- 🐕 **看门狗**（`watchdog.py`，crontab 周期跑）：
  1. aria2 RPC 掉线自愈（执行 `watchdog.restart_cmd`）
  2. 0 速卡死救援（补 tracker → 重连一次 → 才告警，不反复横跳）
  3. 下载完成自动进校验队列，下一轮自动校验归档（`auto_verify:false` 可关）
  4. 磁盘水位熔断（低于 `min_free_gb` 暂停全部任务，不删文件）
  5. 剧集追更扫描 + 机场配额告警
  6. 告警 4h 去重防刷屏，全动作 `watchdog.log` 可审计
- 🔧 **部署自检**：`selftest.py` 缺什么报什么（RPC/ffprobe/目录可写/磁盘水位/mutagen）

## 快速上手

```bash
# 1. 依赖 (Linux NAS; Windows 装 aria2+ffmpeg 同理)
apt install aria2 ffmpeg python3-pip   # 或用户态二进制
pip install musicdl mutagen            # 仅音乐功能需要

# 2. 配置 scripts/config.json: 目录 / RPC secret / 通知 / watchdog
# 3. 自检 + 装看门狗
python3 scripts/selftest.py
python3 scripts/watchdog.py install    # 打印 crontab 行, 粘贴进 crontab -e

# 4. 用法示例
python3 scripts/pan_pipeline.py search "沙丘2" --disk quark,uc     # 网盘通道: 搜链
python3 scripts/pan_pipeline.py check "https://pan.quark.cn/s/xxxx" # 验链+列真实文件树
python3 scripts/pan_pipeline.py save  "https://pan.quark.cn/s/xxxx" --keep "2160p"
python3 scripts/pan_pipeline.py fetch /quark/影视转存/xxx/yyy.mkv   # OpenList直链→aria2
python3 scripts/movie_pipeline.py download "<magnet>" "盗梦空间 (2010) Inception"
python3 scripts/tv_pipeline.py add "<magnet>" "曼达洛人 S01" --ep 1-8
python3 scripts/tv_pipeline.py scan 曼达洛人 --season S01 --expect 8
python3 scripts/tv_pipeline.py follow 曼达洛人 "<发布页URL>" --auto
```

完整工作流（含 agent 编排方式、选版细则、校验标准、归档规范）见 [SKILL.md](SKILL.md)；把本 skill 交给另一台机器上的 AI agent 接管时，让对方先读 [references/ai-handoff.md](references/ai-handoff.md)。

## 实战验证记录

### 案例：盗梦空间 Inception (2010) 4K REMUX — 2026-09-02

| 项 | 数值 |
|---|---|
| 版本 | Inception.2010.2160p.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-FGT |
| 体积 | 79.57 GiB（与源逐字节一致） |
| 下载耗时 | 70 分钟（速度 3→23 MB/s 爬升，BT 马太效应） |
| 校验 | 3840×2160 HEVC 10bit / 148.1min / 开中尾解码零错误 / BT哈希通过 |
| 结论 | ✅ 四道校验全过，可正常观看 |

经验：BT 速度呈马太效应，评估 ETA 要留余量；REMUX 无封装中文字幕（原盘特性），需外挂 SRT。

### 网盘通道 API 实测 — 2026-09-16

- PanSou 聚合（s.panhunt.com，免鉴权）：「沙丘2」命中 47 条（quark 22/magnet 7/115 6/ali 4/…），解析键是 `data.merged_by_type.<disk>[]`（**无 `data.results`**，老 README 会误导）
- 夸克公开 API（token→detail，免登录）：链接有效性 + 递归展开目录 + 真实体积全部拿到（实测一条 28.9GB 合集含 2160p HDR x265 + 1080p WEB-DL + 中英字幕）
- 环境侧 OpenList(QuarkTV 驱动)→aria2 拉取路径：脚本已就绪，NAS 部署完成后回归

### v1.1.0 电视剧+看门狗 (2026-09-16)

10 项离线冒烟测试全过（真 ffmpeg 合成样片跑通 校验→归档→写库→隔离→盘点 全链路；测试中发现并修复 HTML 实体 `&amp;` 导致磁力文件名解析失败的 bug）。aria2 RPC 交互路径待 NAS 实机回归。

## 部署边界（设计原则）

- ✅ **核心层（本项目负责）**：搜索选版 / 下载触发 / 校验门禁 / 归档命名 / 看门狗策略 / 库刷新
- ❌ **环境层（自备）**：下载引擎安装启动、目录创建、权限设置、进程自启、通知通道——NAS 固件权限系统差异太大（飞牛 TrimACL/群晖 synoacltool/威联通 QACL 只认 root），适配它们会让 skill 膨胀成固件手册。一次性工作自己做反而快，`selftest.py` 会告诉你缺什么。

## 仓库结构

```
SKILL.md                      # 完整工作流文档 (AI agent 读这份)
scripts/movie_pipeline.py     # 电影: search/pick/download/status/verify
scripts/pan_pipeline.py       # 网盘通道: search/check/save/fetch/status
scripts/tv_pipeline.py        # 电视剧: pick/add/progress/verify/scan/follow
scripts/watchdog.py           # 看门狗: run(crontab)/status/install/test-notify
scripts/verify_media.py       # 音乐五道校验器
scripts/library_refresh.py    # Emby/Jellyfin/Plex 增量刷新钩子
scripts/selftest.py           # 部署自检
scripts/config.json           # 配置模板
scripts/nas-media-download.md # NAS(飞牛OS)部署实录+tracker池+搜索代理方案
references/musicdl-core.md    # 音乐下载坑库
references/music-optimization.md
references/optimization-playbook.md  # 找不到资源/下载慢进阶手册
references/pan-quark-channel.md      # 网盘通道实测记录: 各家可接管性/API字段/OpenList部署/非会员限制
references/faq-troubleshooting.md    # 他人部署FAQ
references/ai-handoff.md      # AI-to-AI 交接指南
```

## ⚖️ 免责声明

1. **学习与交流用途**：本项目（含全部代码、文档与配置模板）仅供**个人学习、研究与技术交流**使用。通过使用本项目所下载的任何内容，你确认仅用于在**你所在国家或地区法律允许的范围内**评估作品，包括但不限于：购买正版前的试用评估、下载**公有领域（public domain）**或**已获授权**的影音内容、备份**你本人已合法持有**的介质。
2. **请支持正版**：对于任何你打算长期保留或反复观看/收听的内容，请通过合法渠道购买正版（流媒体订阅、数字商店、蓝光/DVD/CD 等）。版权方的劳动是这些作品存在的根基——本项目作者亦以购买正版为原则。下载评估后请在 **24 小时内删除**相关文件。
3. **禁止的行为**：严禁将本项目用于**分发、上传、公开传播、共享账号、商业营利、破解 DRM** 或任何侵犯著作权人合法权益的行为。BT 下载中"上传"是协议固有行为，请知悉你所在司法辖区对此的规定并自行评估风险。
4. **不提供资源**：本项目**不含任何受版权保护的媒体文件、种子或磁力链接**；文档中出现的站点、tracker、示例影片仅为技术说明。你使用本项目访问何种资源、是否拥有合法权利，由你自行负责。
5. **风险自负**：本项目按"现状"提供，作者不对使用本项目导致的任何直接/间接损失（包括但不限于设备损坏、数据丢失、账号封禁、法律纠纷）承担责任。
6. **合规使用**：使用前请了解并遵守你所在国家/地区的法律法规（例如中国大陆的《著作权法》《信息网络传播权保护条例》等）。**如你不同意以上任何一条，请立即停止使用并删除本项目。**

## License

MIT（代码本身）。媒体内容的版权归原权利人所有。
