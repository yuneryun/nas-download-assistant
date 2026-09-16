# 网盘下载通道（夸克为主）— 实测记录与坑库

> 2026-09-16 实测建立。核心结论：**无会员条件下，夸克（及同系 UC）是唯一可自动化接管的国内网盘通道。**

## 各家网盘可接管性（2026-09 核查）

| 网盘 | 无会员 | 依据 |
|---|---|---|
| 夸克 quark | ✅ 主力通道 | 转存即得1TB空间；官方称普通用户不限速，第三方直链实测3~15MB/s；分享验证 API 公开无需登录；OpenList 有官方 Quark/QuarkTV 驱动 |
| UC（阿里系，夸克同资源群常见） | ✅ 兼容 | PanSou 可搜到；分享页结构与夸克同系 |
| 115 | ❌ | **API 开放平台 2026-08-09 起暂停服务**（官方公告），第三方自动化全废；普通用户实测 140~150KB/s |
| 百度 | ❌ 不接 | 非 SVIP 限速依旧；"官方免费提速"=Windows 客户端 P2P 共享上传带宽，无法无头自动化；>20MB 文件下载还须带 `User-Agent: pan.baidu.com` |
| 阿里云盘 | ⚠️ 边缘 | 第三方下载走"会员授权码"体系，免费号被限速（政策常变，动手前实测） |

## 非会员硬限制（自动化必须处理）

- **转存单文件上限 ~40GB** → 超大 4K REMUX（50-80GB）走不了网盘，必须回 BT 通道；1080p 剧集/大部分 4K 压制没问题
- **每日转存次数配额**（数字随政策浮动）→ save 失败且 message 含 limit/频/限 字样 = 配额尽，**停止当轮，次日再跑，绝不重试轰炸**（防风控）
- 夸克转存是异步任务：save 返回 `task_id`，要轮询 `/1/clouddrive/task` 到 `finish`，**并读回目标目录确认落盘**（外部转存的顶层名字可能与分享内文件名不同）

## PanSou 聚合搜索（实测 2026-09-16）

- API：`GET https://s.panhunt.com/api/search?kw=<关键词>`（另有 pansou.app 同源部署），**无需鉴权**
- 响应结构：`data.merged_by_type.<disk>[]`，disk ∈ quark/uc/aliyun/baidu/115/xunlei/123/tianyi/magnet…
  - 每项：`{url, password, note, datetime, source}` —— 标题在 `note`（可能为空），`url` 即分享链
  - ⚠️ 没有 `data.results` 这个键（老版 README 有），别按 README 解析
- 实测「沙丘2」：total=47，quark=22 条；note 质量参差（TG 频道抓取），**必须逐条 check 后才能采信**

## 夸克分享验证（公开 API，无需登录，实测 2026-09-16）

```
POST https://drive.quark.cn/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc
     body: {"pwd_id": "<URL里/s/后面那段>", "passcode": ""}   → data.stoken
GET  https://drive.quark.cn/1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc
     &pwd_id=..&stoken=..&pdir_fid=<目录fid,根=0>&_page=1&_size=100
```
- 支持分页；**目录要递归展开**（用每项的 fid 再查 detail）才能拿到真实体积/文件列表
- 链接失效 → token 接口 code≠0；带提取码 → passcode 填进去
- 用途：过滤死链、列出 mkv 体积、决定转存哪些子文件（省配额）

## 转存（需夸克 Cookie）

- save：`POST https://drive.quark.cn/1/clouddrive/share/sharepage/save?fr=pc&pr=ucpro`，
  body `{pwd_id, fids:[...], to_pdir_fid, scene:"link"}`，带网页端整条 Cookie
- 目录 fid 解析：逐级列目录（`GET /1/clouddrive/files?pdir_fid=..`）找同名子目录，没有就 mkdir（`POST /1/clouddrive/file` + `fmt_type:"folder"`）
- Cookie 获取：网页登录 pan.quark.cn → F12 Network → 任意 drive.quark.cn 请求 → 复制整条 Cookie 存 config（**会过期，失效表现为 code≠0 且带登录字样 → 提醒用户重抓，不要静默失败**）

## OpenList 出直链 → aria2 入库（环境层部署）

- Alist 原项目停更，社区续命版 = **OpenListTeam/OpenList**（v4.2.6, 2026-09-01，活跃）
- 用户态单二进制部署（无 sudo 场景，与 aria2 栈同款套路）：
  1. 解压 `openlist-linux-amd64.tar.gz` 到 `/vol1/1000/openlist/`
  2. `./openlist server` 首启会打印随机管理员密码 → 登录 :5244 改密码
  3. 存储→添加：**QuarkTV 驱动**（手机 App 扫二维码授权，免 Cookie 抓取），挂载路径 `/quark`
  4. 设置里开启「aria2 RPC 对接」可把下载指回本机 aria2（本方案实际走 API 直链，不依赖这个）
  5. `setsid + crontab @reboot` 持久化；`export HOME=/vol1/1000`（飞牛家目录坑同前）
- 取直链：`POST http://127.0.0.1:5244/api/fs/get {path}` → `data.raw_url` → aria2 `addUri` 带 `split=8` 多线程拉
- ⚠️ 已知 issue #666：OpenList 4.0.8 夸克驱动「发送到 aria2」部分文件失败（Alist 3.40 正常）→ **走 fs/get 拿直链自己喂 aria2 的路线更稳，别用 WebUI 的发送到 aria2 按钮**
- 已知：`proxy_new_algo`/302 策略若被网盘风控，可回退默认 302

## 与六段流程的接合点

| 段 | BT 通道 | 网盘通道 |
|---|---|---|
| ① 搜索 | knaben/TPB | PanSou（`pan_pipeline.py search`） |
| ② 选版 | 做种数/ETA 规则 | 有效性+体积+画质关键词；**>40GB 单文件 → 回 BT** |
| ③ 下载 | aria2 磁力 | save 转存 → OpenList raw_url → aria2 addUri（同一 aria2 栈！） |
| ④ 校验 | ffprobe 四道 | **完全复用** |
| ⑤ 归档 | 三件套 | **完全复用**（入库后可顺手删网盘侧转存副本？—— 不删，铁律：网盘侧也只做本地不动云端，留用户处置） |
| ⑥ 通知/看门狗 | watchdog | 同栈（aria2 队列统一被盯）+ 转存配额告警 |

选通道建议：剧集(1080p)/新片（BT 做种少但网盘链接多）→ 网盘优先；老片高码 REMUX(>40GB) → BT 优先；两路都可用时出对比表让用户挑。

## 脚本

`scripts/pan_pipeline.py`：`search / check / save / fetch / status` 五个子命令，与 movie/tv pipeline 共用 config.json。新增配置键：`pan_pansou_api`、`pan_quark_cookie`（仅 save 需要）、`pan_save_root`、`pan_openlist_url`、`pan_openlist_token`（仅 fetch 需要）。

## 本环境（飞牛OS 192.168.1.3）实机部署记录 — 2026-09-17

- 位置：`/vol1/1000/openlist/`（openlist v4.2.6 单二进制+sqlite），**HOME 必须 export 为该目录**（飞牛家目录坑）
- 自启：`/vol1/1000/openlist/start-stack.sh`（crontab @reboot）= openlist(:5244) + **自建 aria2 RPC(:16801, --rpc-listen-all, dir=影视/下载暂存)**
  - ⚠️ :16800 属另一 agent（ClawBot/openclaw 应用，save-session 模式），**端口分开、互不接管互不杀**
- 驱动 API：`POST /api/admin/storage/create`（**是 create 不是 add**）；`delete?id=N` 在 v4.2.6 静默失效 → 用 `disable?id=N`
- QuarkTV 扫码可全程走 API：create 响应 500 message 里带 `data:image/jpeg;base64,` 二维码 → 存图给用户扫 → `disable`+`enable` 重跑 Init → `refresh_token/device_id` 自动填充、status=work
- **实测速度（无会员，48MB 样本）**：单连接 ~25KB/s；4 路 range 并行 ~90KB/s；aria2 split=8 观测 0.1~0.4MB/s 且随时间衰减，最终字节数与源一致
  - 结论：夸克 TV 直链是**按连接+按账号**限速，非会员天花板就是 0.1-0.4MB/s → 20GB 级 ≈ 1-2 天，"挂着慢慢下"成立；>40GB REMUX 仍走 BT；开夸克 VIP(50MB/s) 才有质变
- 已验链路（NAS 本机执行，无需任何代理）：`search`(PanSou 直连 OK) → `check`(夸克公开 API) → `fs/get`(raw_url=dl-c-*.pds.quark.cn) → `aria2` 完成
- **未闭环一步**：save 自动转存需要夸克网页端 Cookie 填 `pan_quark_cookie`（用户抓一次）；没有它只能拉"已在自己网盘里"的文件
- 凭据落盘位置（均 NAS 本地，不过网络）：openlist admin 密码=首次启动打印；token=`/vol1/1000/media-downloader/.ol_token`；aria2 secret+全配置=`/vol1/1000/media-downloader/scripts/config.json`
