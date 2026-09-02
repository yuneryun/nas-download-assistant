# AI-to-AI 交接指南（发给别的 NAS 上的 agent）

> 本 skill 设计为**可被另一个 AI agent 直接接管**。这份文档就是交接说明——如果你是收到本 skill 的 agent，按下面步骤走。

## 你（接收方 agent）需要做的事，按顺序

### 1. 环境自检（先跑这个）
```bash
python3 scripts/selftest.py
```
看输出：
- 全绿 → 直接跳到第 3 步
- 有 ❌ → 按提示修。**注意：修环境（装 aria2/ffmpeg、建目录、调权限）是你作为 agent 的职责**，用你的 shell 工具解决：
  - 缺 aria2/ffmpeg → `apt install aria2 ffmpeg`（无 sudo 则找静态二进制或让用户装）
  - 目录不存在/不可写 → `mkdir -p` + `chmod/chown/setfacl`（你的固件怎么管权限就怎么调，没有 sudo 就找固件的 ACL 工具或让用户在管理界面授权）
  - RPC 不通 → 本 skill 不负责装 aria2，若用户 NAS 没有，装好后按 `scripts/nas-media-download.md` 的启动命令起

### 2. 填配置
编辑 `scripts/config.json`：
- `download_dir_movies` / `download_dir_music` → 你 NAS 上的实际路径（问用户）
- `aria2_rpc` / `aria2_secret` → 本机 aria2 的 RPC（自己起的就用自己设的 secret）
- `min_free_gb` → 按用户磁盘调
- `notify_cmd` → 问用户要不要通知，要的话写推送命令

### 3. 验收
- 再跑一次 `selftest.py` 全绿
- 小文件试下：找个公开种子的磁力链（如法律允许的测试 torrent）走一次 `download → status → verify` 全流程
- 然后就可以正常接用户需求了

## 给你的工作流要点（浓缩版，细节看 SKILL.md）

1. **搜索选版**：多站点搜 → 给用户版本对比表（画质/体积/做种/ETA）→ 用户规则：画质体积优先可牺牲速度，做种≥30 直下，ETA>24h 才降级
2. **下载**：磁力链丢 aria2 RPC，冷启动 0 peer 先等 2 分钟再补 tracker（池在 nas-media-download.md）
3. **校验是硬门禁**：下载完成≠任务完成。ffprobe 元数据（分辨率/码率/时长对得上真实片长）+ 开中尾三段解码实测，全过才算完
4. **归档**：改名 `中文名 (年份) 英文名.mkv` + 入库清单查重
5. **音乐**：musicdl + mutagen 校验，「下A得B」必防（下载后读元数据核对），音质保底策略——无损没有就降级 320k→任意有损，保证能下到

## 常见坑（你都可能踩）

- **固件双层 ACL**（飞牛 TrimACL 等）：setfacl 通了还报 EACCES → 查固件专有 ACL 工具（需 root/网页端），案例见 faq-troubleshooting.md §4.3
- **无家目录账户**：任何写 HOME 的工具先 `export HOME=<可写目录>`
- **SSH 后台进程**：`setsid ... </dev/null &`，nohup 会随通道死
- **GitHub 拉取失败**：国内网络/老 git 都常见，用 ghproxy 镜像或 tarball，见 SKILL.md「部署第 0 步」

## 你不需要做的

- 不需要适配任何特定 NAS 固件的权限系统——那是用户环境层
- 不需要装 qBittorrent（可选引擎，aria2 够用）
- 不需要动 skill 的核心逻辑——选版规则和校验标准是作者实测沉淀，照用
