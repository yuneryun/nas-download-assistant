#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pan_pipeline.py — 网盘下载通道（夸克/UC 为主），与 BT 通道并列接入六段流程。

链路：search(PanSou聚合) → check(夸克公开API验链+列文件) → save(夸克转存,需Cookie)
      → fetch(OpenList出直链 → aria2 RPC 多线下载) → 校验/归档复用 movie/tv pipeline。

设计前提（2026-09 实测）：
- PanSou API 无需鉴权；夸克 sharepage token/detail 无需登录即可验链、列目录。
- 转存(save)与拉直链(fetch)分别需要：夸克 Cookie（仅 save 需要）、OpenList 管理员 token。
- 非会员约束：转存单文件上限 ~40GB（超大 REMUX 请回 BT 通道）；每日转存有配额，
  报 code!=0 且含 限/forbidden 字样时视为配额尽，停止当轮并告警，勿重试轰炸。

子命令：
  search <关键词> [--disk quark,uc,aliyun,baidu,115,xunlei,magnet] [--json out.json]
  check  <分享URL> [--pwd 提取码] [--json out.json]
  save   <分享URL> [--to /转存目录] [--keep 文件名子串]   # 只转存含 keep 的子集，省配额
  fetch  <转存后网盘内路径> [--rename 目标名.mkv]          # OpenList直链→aria2
  status                                                      # aria2 活动任务
  batch  <search.json> [--pick N]                              # 串起来：搜索→验→存→拉

配置（config.json，与 movie_pipeline 共用）：
  pan_pansou_api     默认 https://s.panhunt.com/api/search
  pan_quark_cookie   夸克网页端登录后的完整 Cookie 串（仅 save 需要）
  pan_save_root      转存落根目录，如 /影视转存
  pan_openlist_url   如 http://127.0.0.1:5244
  pan_openlist_token OpenList 管理员 token（仅 fetch 需要）
  aria2_rpc / aria2_secret  沿用现有键
"""
import json, os, re, sys, time, gzip, io, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.environ.get("MEDIA_DL_CONFIG", os.path.join(HERE, "config.json"))

def cfg():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

C = cfg()

def http(url, data=None, headers=None, timeout=30, method=None):
    h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
         "Accept-Encoding": "gzip"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=(json.dumps(data).encode() if isinstance(data, (dict, list)) else data),
                                 headers=h, method=method or ("POST" if data is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return json.loads(raw.decode("utf-8", "replace"))

def eprint(*a): print(*a, file=sys.stderr)

# ---------------- ① 搜索：PanSou 聚合 ----------------
def cmd_search(kw, disks=("quark", "uc"), as_json=None):
    api = C.get("pan_pansou_api", "https://s.panhunt.com/api/search")
    j = http(api + "?" + urllib.parse.urlencode({"kw": kw}))
    if j.get("code") != 0:
        raise SystemExit(f"PanSou 返回异常: {j.get('message')}")
    merged = j.get("data", {}).get("merged_by_type", {})
    out = []
    for d in disks:
        for it in merged.get(d, []):
            out.append({"disk": d, "url": it.get("url", ""),
                        "password": it.get("password", ""),
                        "note": (it.get("note") or "").strip(),
                        "source": it.get("source", "")})
    print(f"关键词「{kw}」: " + ", ".join(f"{k}={len(v)}" for k, v in merged.items() if v))
    for i, it in enumerate(out):
        print(f"[{i}] ({it['disk']}) {it['note'][:60]}  {it['url']}")
    if as_json:
        with open(as_json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        eprint(f"→ 已写 {as_json} ({len(out)} 条)")
    return out

# ---------------- ② 验证：夸克公开 API ----------------
QUARK_PWD_RE = re.compile(r"pan\.quark\.cn/s/([0-9a-zA-Z]+)")

def quark_pwd_id(url):
    m = QUARK_PWD_RE.search(url)
    if not m:
        raise SystemExit(f"不是夸克分享链接: {url}")
    return m.group(1)

def cmd_check(url, pwd="", as_json=None):
    """无需登录：返回链接是否有效 + 文件树(名称/大小)。"""
    pid = quark_pwd_id(url)
    t = http("https://drive.quark.cn/1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc",
             {"pwd_id": pid, "passcode": pwd})
    data = t.get("data") or {}
    stoken = data.get("stoken")
    if not stoken:
        return {"valid": False, "why": t.get("message"), "url": url}

    def list_dir(pdir_fid):
        files, page = [], 1
        while True:
            q = urllib.parse.urlencode({"pr": "ucpro", "fr": "pc", "pwd_id": pid,
                                        "stoken": stoken, "pdir_fid": pdir_fid, "force": "0",
                                        "_page": page, "_size": 100, "_sort": "file_name:asc"})
            d = http("https://drive.quark.cn/1/clouddrive/share/sharepage/detail?" + q)
            lst = (d.get("data") or {}).get("list", [])
            files += lst
            total = (d.get("metadata") or {}).get("_total") or len(files)
            if len(files) >= total or not lst:
                break
            page += 1
        return files

    flat, stack = [], [(f, "") for f in list_dir("0")]
    while stack:
        f, prefix = stack.pop()
        f["_path"] = prefix + f.get("file_name", "")
        flat.append(f)
        if f.get("dir"):
            for sub in list_dir(f["fid"]):
                stack.append((sub, f["_path"] + "/"))
    tree = [{"name": f["_path"], "fid": f.get("fid", ""), "path_fid": f.get("path_fid", ""),
             "dir": bool(f.get("dir")), "size": f.get("size", 0)} for f in flat]
    gb = sum(f["size"] for f in tree if not f["dir"]) / 1e9
    over = [f for f in tree if f["size"] > 40e9]
    r = {"valid": True, "url": url, "note": data.get("author", {}).get("nick_name", ""),
         "total_gb": round(gb, 1), "files": tree,
         "warn_over40g": [f["name"] for f in over]}
    print(f"✅ 有效 | 共{len(tree)}项 {gb:.1f}GB" + (f" ⚠️超40G单文件:{r['warn_over40g']}" if over else ""))
    for f in tree[:40]:
        print(("  [DIR] " if f["dir"] else f"  [{f['size']/1e9:.1f}G] ") + f["name"])
    if as_json:
        with open(as_json, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    return r

# ---------------- ③ 转存：夸克 save（需 Cookie） ----------------
def cmd_save(url, pwd="", to=None, keep=None):
    cookie = C.get("pan_quark_cookie")
    if not cookie:
        raise SystemExit("缺 pan_quark_cookie（config.json）。获取：网页登录夸克→DevTools→Network→任意 drive.quark.cn 请求→复制整条 Cookie")
    pid = quark_pwd_id(url)
    chk = cmd_check(url, pwd)
    if not chk["valid"]:
        raise SystemExit(f"链接无效: {chk.get('why')}")
    candidates = [f for f in chk["files"] if (keep is None or keep in f["name"])]
    # 只取最浅层：父目录已入选则跳过其子孙，避免重复转存
    by_depth = sorted(candidates, key=lambda f: f["name"].count("/"))
    targets, chosen = [], []
    for f in by_depth:
        if any(f["name"].startswith(c + "/") for c in chosen):
            continue
        targets.append(f); chosen.append(f["name"])
    if not targets:
        raise SystemExit(f"--keep {keep!r} 未命中任何文件")
    to = to or C.get("pan_save_root", "/影视转存")
    pdir_fid = _resolve_dir_fid(to, cookie)
    body = {"pwd_id": pid, "fids": [f["fid"] for f in targets],
            "to_pdir_fid": pdir_fid, "scene": "link"}
    if pwd:
        body["passcode"] = pwd
    r = http("https://drive.quark.cn/1/clouddrive/share/sharepage/save?fr=pc&pr=ucpro",
             body, headers={"Cookie": cookie})
    if r.get("code") != 0 or r.get("status") != 200:
        msg = str(r.get("message", ""))
        hint = "（疑似转分配额用尽——非会员每日有限，明日再试或升级会员）" if any(
            w in msg.lower() for w in ("limit", "forbidden", "频", "限")) else ""
        raise SystemExit(f"转存失败: {msg}{hint}")
    # 夸克转存是异步任务：轮询 task 接口直到完成
    task_id = ((r.get("data") or {}).get("task_id")) or ""
    if task_id:
        for _ in range(30):
            tr = http("https://drive.quark.cn/1/clouddrive/task?fr=pc&pr=ucpro&task_id="
                      + urllib.parse.quote(task_id), headers={"Cookie": cookie})
            if ((tr.get("data") or {}).get("finish", True)):
                break
            time.sleep(2)
    # 读回目标目录，输出实际落盘条目（避免依赖"转存后叫什么名"的假设）
    names = []
    page = 1
    while True:
        q = urllib.parse.urlencode({"pr": "ucpro", "fr": "pc", "pdir_fid": pdir_fid,
                                    "_page": page, "_size": 200, "_sort": "updated_at:desc"})
        lr = http("https://drive.quark.cn/1/clouddrive/files?" + q, headers={"Cookie": cookie})
        lst = (lr.get("data") or {}).get("list") or []
        for f in lst:
            names.append((f.get("file_name", ""), bool(f.get("dir"))))
        total = (lr.get("metadata") or {}).get("_total") or 0
        if page * 200 >= total or not lst:
            break
        page += 1
    print(f"✅ 转存完成 → 夸克目录 {to}（异步任务已确认）")
    print("该目录当前条目（最新在前，前10）：")
    for n, d in names[:10]:
        print(("  [DIR] " if d else "  [FILE] ") + n)
    return to

def _resolve_dir_fid(path, cookie):
    """逐级查/建夸克目录，返回最终 fid（根=0）。用目录列表接口在每层找同名子目录。"""
    fid = "0"
    for seg in [s for s in path.split("/") if s]:
        found = None
        page = 1
        while True:
            q = urllib.parse.urlencode({"pr": "ucpro", "fr": "pc", "pdir_fid": fid,
                                        "_page": page, "_size": 200, "_sort": "file_type:asc,file_name:asc"})
            lr = http("https://drive.quark.cn/1/clouddrive/files?" + q, headers={"Cookie": cookie})
            lst = (lr.get("data") or {}).get("list") or []
            for f in lst:
                if f.get("dir") and f.get("file_name") == seg:
                    found = f.get("fid"); break
            total = (lr.get("metadata") or {}).get("_total") or 0
            if found or page * 200 >= total or not lst:
                break
            page += 1
        if not found:
            cr = http("https://drive.quark.cn/1/clouddrive/file?fr=pc&pr=ucpro",
                      {"pdir_fid": fid, "file_name": seg, "dir_path": "", "dirinit": 0,
                       "size": 0, "fmt_type": "folder"},
                      headers={"Cookie": cookie})
            found = ((cr.get("data") or {}).get("fid")) or "0"
        fid = found
    return fid

# ---------------- ④ 拉取：OpenList 直链 → aria2 ----------------
def ol(path, sub, payload):
    base = C.get("pan_openlist_url", "http://127.0.0.1:5244").rstrip("/")
    return http(base + "/api" + sub, payload,
                headers={"Authorization": C.get("pan_openlist_token", "")})

def cmd_fetch(pan_path, rename=None):
    """pan_path: OpenList 中夸克挂载下的路径，如 /quark/影视转存/沙丘2/xxx.mkv"""
    j = ol("", "/fs/get", {"path": pan_path, "password": ""})
    if j.get("code") != 200:
        raise SystemExit(f"OpenList fs/get 失败: {j.get('message')}")
    raw = j["data"]["raw_url"]
    size = j["data"].get("size", 0)
    if size > 40e9:
        eprint(f"⚠️ 该文件 {size/1e9:.1f}GB 超夸克非会员单文件限制，直链可能不稳；建议回 BT 通道")
    rpc = C.get("aria2_rpc", "http://127.0.0.1:16800").rstrip("/") + "/jsonrpc"
    secret = C.get("aria2_secret", "")
    out = rename or os.path.basename(pan_path)
    # 多线程钉死 16（夸克按连接限速, 连接越多合计越快; aria2 上限即 16）
    r = http(rpc, {"jsonrpc": "2.0", "id": "1", "method": "aria2.addUri",
                   "params": ["token:" + secret if secret else secret, [raw],
                              {"out": out, "dir": C.get("download_dir_movies", "."),
                               "split": "16", "max-connection-per-server": "16",
                               "min-split-size": "8M", "summary": f"[pan] {out}"}]})
    gid = r.get("result", "")
    print(f"✅ aria2 已入队 {out} ({size/1e9:.1f}GB) gid={gid}")
    return gid

def cmd_status():
    rpc = C.get("aria2_rpc", "http://127.0.0.1:16800").rstrip("/") + "/jsonrpc"
    secret = C.get("aria2_secret", "")
    r = http(rpc, {"jsonrpc": "2.0", "id": "1", "method": "aria2.tellActive",
                   "params": ["token:" + secret if secret else secret,
                              ["gid", "status", "totalLength", "downloadLength", "downloadSpeed"]]})
    for t in r.get("result", []):
        mb = float(t["downloadSpeed"]) / 1048576
        remain = (int(t["totalLength"]) - int(t["downloadLength"])) / max(float(t["downloadSpeed"]), 1)
        print(f"  {t['gid'][:10]} {int(t['downloadLength'])/1e9:.1f}/{int(t['totalLength'])/1e9:.1f}GB "
              f"{mb:.1f}MB/s ETA {remain/3600:.1f}h")
    if not r.get("result"):
        print("  (无活动任务)")

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); return
    cmd, args = sys.argv[1], sys.argv[2:]
    def flag(name, default=None):
        return args[args.index(name) + 1] if name in args else default
    pos = [a for a in args if not a.startswith("--")]
    if cmd == "search":
        cmd_search(pos[0], tuple((flag("--disk") or "quark,uc").split(",")), flag("--json"))
    elif cmd == "check":
        cmd_check(pos[0], flag("--pwd", ""), flag("--json"))
    elif cmd == "save":
        cmd_save(pos[0], flag("--pwd", ""), flag("--to"), flag("--keep"))
    elif cmd == "fetch":
        cmd_fetch(pos[0], flag("--rename"))
    elif cmd == "status":
        cmd_status()
    else:
        print(__doc__); raise SystemExit("未知子命令: " + cmd)

if __name__ == "__main__":
    main()
