# 他人部署常见问题排查手册（FAQ）

> 给拿到 nas-download-assistant 的使用者。按"装完跑不起来"的实际概率排序。

## 一、拉取仓库就失败（最常见的第一道坎）

### 1.1 国内网络拉不动 GitHub
**症状**：`git clone` 超时 / `Connection reset` / `SSL error`
**原因**：github.com 在国内时通时不通（DNS 污染 + 线路抖动）
**解法（按推荐排序）**：
```bash
# A. 换镜像站（最快）
git clone https://ghproxy.net/https://github.com/yuneryun/nas-download-assistant.git
# 或 gitclone.com 镜像
git clone https://gitclone.com/github.com/yuneryun/nas-download-assistant.git

# B. 直接下 tarball（绕过 git 协议问题，老系统/精简 git 都能跑）
curl -L https://github.com/yuneryun/nas-download-assistant/archive/refs/heads/main.tar.gz -o mda.tar.gz
tar xzf mda.tar.gz

# C. 有代理的话
git clone https://github.com/yuneryun/nas-download-assistant.git --config "http.proxy=socks5://127.0.0.1:7890"
```
**skill 作者建议**：非 git 用户直接下 tarball，少一个依赖少一个坑。

### 1.2 NAS 上的 git 太老/不支持 HTTPS
**症状**：`fatal: unable to access ... SSL library problem`、`gnutls` 相关报错
**原因**：部分 NAS 固件（群晖/飞牛精简环境）的 git+curl 库老旧，证书链不全
**解法**：跳过 git——在任意电脑 clone 后打 tar 包传 NAS（scp/SMB 都行），或直接 GitHub 网页 Download ZIP

## 二、装依赖的坑

### 2.1 aria2 没装 / 版本太老
```bash
apt install aria2          # Debian/Ubuntu NAS
# Alpine/精简固件: 用静态二进制
curl -L https://github.com/abcfy2/aria2-static-build/releases -o aria2.tar.xz
```
注意：**aria2 无需 systemd 服务**，`setsid aria2c ... &` 后台跑即可（skill 脚本已内置）。

### 2.2 ffmpeg/ffprobe 没装
校验器依赖它。精简 NAS 常缺：
```bash
apt install ffmpeg
# 装不上的最小方案: 下载静态版 johnvansickle.com/ffmpeg (Linux)
```

### 2.3 Python 依赖
```bash
pip install mutagen musicdl   # 音乐管线需要
# musicdl 在无家目录账户下崩: 先 export HOME=/tmp 或任意可写目录
```

## 三、配置类问题

### 3.1 config.json 没改
`scripts/config.json` 里 `aria2_secret` 默认 `CHANGE_ME`——**必须改成你 aria2 启动时的 `--rpc-secret`**，否则 RPC 全部 401。
诊断：`curl http://127.0.0.1:16800/jsonrpc -d '{"jsonrpc":"2.0","id":"1","method":"aria2.getVersion","params":["token:你的secret"]}'`

### 3.2 路径权限
- NAS 多用户环境：下载目录当前用户要有**写权限**（`ls -ld <dir>` 看属主）
- 飞牛/群uin 的 `/vol*` 目录常是 ACL 控制，chmod 不一定够，要在管理界面把用户加进对应共享目录的权限组

### 3.3 磁盘水位误判
`min_free_gb` 默认 500，小盘 NAS（<1TB）会被直接拒绝下载。改小到合理值（如 50）。

## 四、运行时问题

### 4.1 磁力冷启动 0 peer（ET：永远 0%）
等 3 分钟 → RPC 补 tracker（skill 文档有现成池）→ 仍 0 则资源已死，换版本。**不要挂着等半天**。

### 4.2 BT 端口不通（速度常年 <1MB/s 且 CN 很低）
路由器放行 26999(TCP/UDP) + 26998(UDP)，有 IPv6 的开 IPv6 入站。这是速度优化最大单项。

### 4.3 下载完成但校验失败
看具体关卡：
- 元数据关：假 4K（WEB-DL 冒充），换真 REMUX 源
- 解码关：文件真损坏，重下
- 哈希关：aria2 会自动重下坏块，反复失败 = 种子本身坏

### 4.4 音乐管线「下A得B」
下载结果歌名不可信是 musicdl 的老毛病，`verify_media.py` 的元数据核对就是为此设计的。FAIL 是正常流程，脚本会自动换源重试。

### 4.5 通知不响
`notify_cmd` 默认空。接 QQ/Telegram/webhook 需自己写一行推送命令填进 config，格式 `{msg}` 占位。

## 五、安全提醒（务必看）

1. **aria2 RPC secret 不要用默认值**，且**不要把 16800 端口映射到公网**（裸 RPC = 任何人可往你 NAS 下载任意文件）
2. 需要外网访问下载进度 → 走 VPN/Tailscale 回家，或 nginx 加 Basic Auth 反代
3. 仓库里 `config.json` 是模板，**自己的真实配置别 commit 回去**

## 六、快速自检

```bash
python scripts/selftest.py
```
全绿再开始用。任何一项 ❌ 按提示修，别带病运行。
