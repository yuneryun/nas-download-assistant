# -*- coding: utf-8 -*-
"""tv_pipeline.py — 电视剧下载一体化: 选版→批量下载→逐集校验→进度/缺集/追更
用法:
  python tv_pipeline.py search <剧名>                  # 打印搜索/选版流程提示
  python tv_pipeline.py pick <versions.json>           # 季版本对比表 + 整季包/分集建议
  python tv_pipeline.py add <磁力链> "<剧名 S01>" [--ep 1-12]   # 加入aria2; --ep按集选文件
  python tv_pipeline.py progress "<剧名 S01>"          # 该季下载进度表(完成/下载中/失败)
  python tv_pipeline.py verify <文件> "<剧名 S01E01>"  # 逐集四道校验+归档到季目录+写库
  python tv_pipeline.py scan <剧名> [--season S01] [--expect 12]  # 缺集盘点
  python tv_pipeline.py follow <剧名> <URL> [--auto]   # 抓页面提取新集磁力; --auto直接加入
依赖: aria2c(RPC常驻), ffprobe/ffmpeg; 配置: config.json (download_dir_tv)
选版规则(与电影一脉相承, 电视剧特化):
  - 整季包做种充足优先整季包; 剧集1080p x265即达标, 神剧才上4K
  - 整季包预估ETA>48h → 降级分集单下
  - 校验失败残件移入 _quarantine 隔离区等用户处置, 绝不删除(用户铁律)
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, urllib.parse

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(HERE, 'config.json')
CONF = json.load(open(CONF_PATH, encoding='utf-8')) if os.path.exists(CONF_PATH) else {}
CONF.setdefault('download_dir_tv', '/vol2/1000/影视/剧集')
CONF.setdefault('aria2_rpc', 'http://127.0.0.1:16800')
CONF.setdefault('aria2_secret', 'fnosdl')
CONF.setdefault('min_free_gb', 500)
CONF.setdefault('library_index', 'media_library.json')
CONF.setdefault('notify_cmd', '')
RPC, SEC = CONF['aria2_rpc'], CONF['aria2_secret']
TVDIR = CONF['download_dir_tv']
QUARANTINE = os.path.join(TVDIR, '_quarantine')
MANIFEST = os.path.join(TVDIR, '.tv_progress.json')
LIB = CONF['library_index'] if os.path.isabs(CONF['library_index']) else os.path.join(HERE, CONF['library_index'])
EP_RE = re.compile(r'[Ss](\d{1,2})[\s._\-]?[Ee](\d{1,3})')
VIDEO_EXT = {'.mkv', '.mp4', '.ts', '.m2ts', '.avi', '.wmv', '.mov', '.iso'}


def rpc(method, params=None, quiet=False):
    body = {"jsonrpc": "2.0", "id": "1", "method": method, "params": ["token:" + SEC] + (params or [])}
    try:
        req = urllib.request.Request(RPC + '/jsonrpc', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if 'error' in resp and not quiet:
            print('❌ aria2 RPC 报错:', resp['error'].get('message'))
            return None
        return resp.get('result')
    except Exception as e:
        if not quiet:
            print(f'❌ aria2 RPC 不可达: {e}\n   先启动 aria2c(见 nas-media-download.md) 或配好 watchdog 的 restart_cmd 让其自愈')
        return None


def parse_ep_range(s):
    """'1-12' / '1,3,5-8' → {1,2,...}"""
    out = set()
    for part in s.split(','):
        if '-' in part:
            a, b = part.split('-', 1)
            out.update(range(int(a), int(b) + 1))
        elif part.strip():
            out.add(int(part))
    return out


def load_manifest():
    try:
        return json.load(open(MANIFEST, encoding='utf-8'))
    except Exception:
        return {}


def save_manifest(m):
    os.makedirs(TVDIR, exist_ok=True)
    json.dump(m, open(MANIFEST, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def load_index():
    try:
        return json.load(open(LIB, encoding='utf-8'))
    except Exception:
        return []


def eta_hours(size_gb, seeders):
    est_speed = min(25, max(0.3, seeders * 0.5))
    return size_gb * 1024 / est_speed / 3600


# ────────────────────────── 选版 ──────────────────────────

def pick(versions_path):
    """versions.json: [{name,size_gb,seeders,kind:'pack'|'split',quality_rank,res}]"""
    vs = json.load(open(versions_path, encoding='utf-8'))
    print(f"{'版本':<58}{'类型':>6}{'体积':>9}{'做种':>5}{'ETA':>7}  建议")
    for v in vs:
        v['eta'] = eta_hours(v['size_gb'], v['seeders'])
        v['tag'] = '⭐画质最佳' if v['quality_rank'] == 0 else ('⚡最快' if v['seeders'] == max(x['seeders'] for x in vs) else '')
        print(f"{v['name'][:56]:<58}{v.get('kind','split'):>6}{v['size_gb']:>7.1f}GB{v['seeders']:>5}{v['eta']:>6.0f}h  {v['tag']}")
    packs = [v for v in vs if v.get('kind') == 'pack']
    # 画质优先: 按 quality_rank 升序找第一个做种≥30且ETA≤48h的整季包(与电影规则一致)
    best_pack = next((v for v in sorted(packs, key=lambda v: (v['quality_rank'], -v['seeders']))
                      if v['seeders'] >= 30 and v['eta'] <= 48), None)
    if best_pack:
        print(f"\n→ 推荐整季包: {best_pack['name']}  (做种≥30且ETA≤48h, 一锅端省管理)")
    else:
        splits = [v for v in vs if v.get('kind') != 'pack'] or vs
        best = sorted(splits, key=lambda v: (v['quality_rank'], -v['seeders']))[0]
        n_ep = int(best.get('episodes', 12))
        print(f"\n→ 推荐分集下载: {best['name']}  (整季包缺位/ETA超限; 单集ETA≈{best['eta']:.0f}h, 共{n_ep}集)")
        print('   add 时用 --ep 分批勾选, 或每集单独磁力逐条 add')


# ────────────────────────── 下载 ──────────────────────────

def wait_metadata(gid, timeout=150):
    """磁力包等元数据就绪, 返回 files 列表(带 path), 超时返回 None"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = rpc('aria2.tellStatus', [gid, ['status', 'name', 'files']], quiet=True)
        if st and st.get('files'):
            first = st['files'][0]
            if first.get('length', '0') != '0' and len(st['files']) > 1:
                return st['files']
            if st.get('name') and not re.fullmatch(r'[0-9a-fA-F]{40}', st['name']):
                return st['files']
        time.sleep(5)
    return None


def add(magnet, label, ep_range=None):
    """成功 True / 失败 False — 绝不 sys.exit(会被 watchdog 当库 import 调用, 退出会杀死看门狗)"""
    season_dir = os.path.join(TVDIR, label)
    os.makedirs(season_dir, exist_ok=True)
    if shutil.disk_usage(TVDIR).free / 1e9 < CONF['min_free_gb']:
        print(f'❌ 磁盘余量不足 {CONF["min_free_gb"]}GB, 拒绝下载'); return False
    gid = rpc('aria2.addUri', [[magnet], {'dir': season_dir, 'seed-time': '0',
                                          'split': '16', 'max-connection-per-server': '16',
                                          'min-split-size': '8M'}])
    if not gid:
        return False
    print(f'✅ 已加入 {label}  GID={gid}')
    man = load_manifest()
    entry = man.setdefault(label, {'episodes': {}, 'magnets': []})
    if magnet not in entry['magnets']:
        entry['magnets'].append(magnet)
    if ep_range:
        want = parse_ep_range(ep_range)
        files = wait_metadata(gid)
        if files:
            picks = []
            for i, f in enumerate(files, 1):
                fn = os.path.basename(f['path'])
                m = EP_RE.search(fn)
                if m and int(m.group(2)) in want:
                    picks.append((i, int(m.group(2)), fn))
            if picks:
                rpc('aria2.remove', [gid], quiet=True)
                gid2 = rpc('aria2.addUri', [[magnet], {'dir': season_dir, 'seed-time': '0',
                                                       'split': '16', 'max-connection-per-server': '16',
                                                       'min-split-size': '8M',
                                                       'select-file': ','.join(str(i) for i, _, _ in picks)}])
                for i, ep, fn in picks:
                    entry['episodes'][f'E{ep:02d}'] = {'gid': gid2, 'idx': i, 'file': fn, 'status': 'downloading'}
                print(f'✅ 已按集选择 {len(picks)} 集 (新GID={gid2}):', ', '.join(f'E{e:02d}' for _, e, _ in picks))
            else:
                print('⚠️ 文件名里没解析出 E01 式集号, 保留整包下载; 下载完成后用 verify 逐集处理')
        else:
            print('⚠️ 元数据等待超时(可能0 DHT连接), 保留整包; 稍后 progress 查看或重下')
    save_manifest(man)
    return True


def progress(label):
    man = load_manifest()
    entry = man.get(label)
    if not entry:
        print(f'清单里没有 {label}; 用 scan {label.split(" ")[0]} 看库内已有'); return
    season_dir = os.path.join(TVDIR, label)
    eps = entry.get('episodes', {})
    if not eps:
        print(f'{label}: 整包模式(未逐集登记). aria2 任务状态:')
        for t in (rpc('aria2.tellActive') or []):
            if label in (t.get('dir') or '').replace('/', os.sep):
                pct = int(t['completedLength']) / max(1, int(t['totalLength'])) * 100
                print(f"  {t.get('name','?')[:50]}  {pct:.1f}%  {int(t['downloadSpeed'])/1e6:.1f}MB/s")
        return
    done = active = 0
    gids = sorted({e['gid'] for e in eps.values() if e.get('gid')})
    st_map = {}
    for g in gids:
        s = rpc('aria2.tellStatus', [g, ['status', 'completedLength', 'totalLength', 'downloadSpeed', 'connections', 'name']], quiet=True)
        st_map[g] = s
    print(f"{'集':>5}  {'状态':<10}{'进度':>8}{'速度':>9}{'连接':>5}  文件")
    for k in sorted(eps):
        e = eps[k]
        fpath = os.path.join(season_dir, e.get('file', ''))
        if e.get('file') and os.path.exists(fpath):
            eps[k]['status'] = 'done'; done += 1
            print(f"{k:>5}  {'✅ 完成':<10}{'':>8}{'':>9}{'':>5}  {e['file'][:44]}")
            continue
        s = st_map.get(e.get('gid'))
        if s and s.get('status') == 'active':
            active += 1
            pct = int(s['completedLength']) / max(1, int(s['totalLength'])) * 100
            print(f"{k:>5}  {'⬇ 下载中':<10}{pct:>7.1f}%{int(s['downloadSpeed'])/1e6:>8.1f}M{s.get('connections','-'):>5}  {e.get('file','')[:44]}")
        elif s:
            print(f"{k:>5}  {'⏸ ' + s['status']:<10}{'':>8}{'':>9}{'':>5}  {e.get('file','')[:44]}")
        else:
            print(f"{k:>5}  {'❓ 任务丢失':<10}{'':>8}{'':>9}{'':>5}  {e.get('file','')[:44]}  (可凭清单里的 magnet 重新 add)")
    save_manifest(man)
    print(f'\n小结: 完成 {done}/{len(eps)}, 下载中 {active}')


# ────────────────────────── 校验+归档 ──────────────────────────

def lib_durations(show, season):
    return sorted(e.get('duration_min', 0) for e in load_index()
                  if e.get('show') == show and e.get('season') == season and e.get('duration_min'))


def verify(path, label=None):
    """label: '剧名 S01E01'; 缺省时从 文件名+父目录名 自动拼. 四道校验 → 归档季目录 / 隔离; 写库"""
    if not label:
        label = os.path.basename(os.path.dirname(path)) + ' ' + os.path.splitext(os.path.basename(path))[0]
    issues = []
    m = EP_RE.search(label) or EP_RE.search(os.path.basename(path))
    if not m:
        print('❌ label/文件名里解析不到 SxxEyy 集号'); return False
    s_e = f'S{int(m.group(1)):02d}E{int(m.group(2)):02d}'
    # 剧名 = 第一个 SxxEyy 之前的部分, 再去掉尾部孤立的季标记("剧名 S01 release名.S01E01..." → "剧名")
    show = re.sub(r'[\s._\-]+[Ss]\d{1,2}[\s._\-]*\S*$', '', label[:m.start()]).strip(' .-_') \
         or label.split(' ')[0]
    season = f'S{int(m.group(1)):02d}'
    # 1 元数据
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=width,height,codec_name,bit_rate', '-show_entries', 'format=duration',
                        '-of', 'json', path], capture_output=True, text=True)
    try:
        meta = json.loads(r.stdout or '{}')
        st = meta['streams'][0]
        w, h = st['width'], st['height']
        dur = float(meta['format']['duration']) / 60
        br = int(st.get('bit_rate') or meta['format'].get('bit_rate') or 0)
        print(f'分辨率:{w}x{h} 编码:{st["codec_name"]} 码率:{br/1e6:.1f}Mbps 时长:{dur:.1f}min')
        if h < 1080:
            issues.append(f'分辨率仅{w}x{h}(<1080p, 低于剧集标准)')
        if h >= 2160 and br and br < 15e6:
            issues.append('4K但码率<15Mbps(假4K嫌疑)')
        durs = lib_durations(show, season)
        if durs:
            med = durs[len(durs) // 2]
            if med and (dur > med * 2.2 or dur < med * 0.45):
                issues.append(f'时长{dur:.0f}min偏离本季中位数{med:.0f}min(合集/删减/错文件嫌疑)')
        elif not (8 <= dur <= 180):
            issues.append(f'时长{dur:.0f}min不在剧集常规区间8-180min')
    except Exception as ex:
        issues.append(f'元数据读取失败:{ex}'); meta = {}; dur = 0
    # 2 解码实测 3 段
    d = int(float((meta.get('format', {}) or {}).get('duration', 0) or 0))
    if d:
        for pos in (60, d // 2, max(0, d - 90)):
            r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(pos), '-i', path, '-t', '30', '-f', 'null', '-'],
                               capture_output=True, text=True)
            if r.stderr.strip():
                issues.append(f'解码错误@{pos}s: {r.stderr[:80]}')
    # 3/4 容器
    r = subprocess.run(['ffprobe', '-v', 'error', path], capture_output=True, text=True)
    if 'Invalid data' in r.stderr:
        issues.append('容器损坏')
    ext = os.path.splitext(path)[1] or '.mkv'
    if issues:
        os.makedirs(QUARANTINE, exist_ok=True)
        dst = os.path.join(QUARANTINE, f'{show} {s_e}_{time.strftime("%m%d%H%M")}{ext}')
        shutil.move(path, dst)
        print('❌ 校验未通过 → 已移入隔离区(未删除):', dst)
        for i in issues:
            print('   └', i)
        notify(f'🚨 剧集校验失败已隔离: {show} {s_e} — {issues[0]}')
        return False
    # 归档: 剧集/S 剧名 (季)/Season XX/剧名 SxxEyy.ext
    season_dir = os.path.join(TVDIR, f'{show} {season}')
    os.makedirs(season_dir, exist_ok=True)
    dst = os.path.join(season_dir, f'{show} {s_e}{ext}')
    shutil.move(path, dst)
    # 写库
    lib = load_index()
    lib = [e for e in lib if not (e.get('title') == f'{show} {s_e}')]
    lib.append({'title': f'{show} {s_e}', 'show': show, 'season': season, 'episode': s_e,
                'path': dst, 'size_gb': round(os.path.getsize(dst) / 1e9, 2),
                'duration_min': round(dur, 1), 'verified': True, 'date': time.strftime('%Y-%m-%d')})
    json.dump(lib, open(LIB, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    notify(f'📺 {show} {s_e} 校验完成 已归档 ({dst})')
    refresh_library(dst)
    print('✅ 校验通过并归档:', dst)
    print('ℹ️ 字幕: 逐集外挂SRT与视频同名放同目录(OpenSubtitles/SubHD); 整季可一次搜齐')
    return True


def notify(msg):
    if CONF.get('notify_cmd'):
        subprocess.run(CONF['notify_cmd'].replace('{msg}', msg), shell=True)


def refresh_library(path=None):
    """归档成功后通知 Emby/Jellyfin/Plex(未配置则静默跳过); 永不抛异常"""
    try:
        import library_refresh
        library_refresh.refresh(path, verbose=False)
    except Exception:
        pass


# ────────────────────────── 缺集盘点 ──────────────────────────

def scan(show, season=None, expect=None):
    found = {}
    needle = show.lower().replace(' ', '').replace('.', '')
    for root, _, files in os.walk(TVDIR):
        # 集的正源是归档后的文件名(剧名 SxxEyy.ext)与目录名(剧名 Sxx); 英文release名不含中文剧名, 靠路径匹配
        if needle not in root.lower().replace(' ', '').replace('.', ''):
            continue
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in VIDEO_EXT:
                continue
            m = EP_RE.search(fn)
            if m:
                key = f'S{int(m.group(1)):02d}E{int(m.group(2)):02d}'
                if season and not key.startswith(season):
                    continue
                found[key] = os.path.join(root, fn)
    for e in load_index():
        if e.get('show') == show and (not season or e.get('season') == season):
            found.setdefault(e['episode'], e['path'])
    for key in sorted(found):
        print('  ✅', key)
    print(f'共 {len(found)} 集')
    if expect:
        seas = season or (sorted(found)[0][:3] if found else 'S01')
        have = {int(k[-2:]) for k in found if k.startswith(seas)}
        missing = [f'{seas}E{i:02d}' for i in range(1, expect + 1) if i not in have]
        print(('⚠️ 缺集: ' + ', '.join(missing)) if missing else f'✅ {seas} 全 {expect} 集齐')


# ────────────────────────── 追更 ──────────────────────────

def fetch(url):
    wd = CONF.get('watchdog', {})
    proxy = wd.get('proxy') or os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or ''
    handlers = [urllib.request.ProxyHandler({'http': proxy, 'https': proxy})] if proxy else []
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    return urllib.request.build_opener(*handlers).open(req, timeout=25).read().decode('utf-8', 'replace')


def follow(show, url, auto=False):
    page = fetch(url)
    magnets = re.findall(r'magnet:\?xt=urn:btih:[^"\'\s<>]+', page)
    man = load_manifest()
    have = set()
    for lbl, entry in man.items():
        if lbl.startswith(show):
            for k, e in entry.get('episodes', {}).items():
                if e.get('status') in ('downloading', 'done'):
                    have.add(k)
    for e in load_index():
        if e.get('show') == show:
            have.add(e.get('episode', ''))
    new = []
    for mg in dict.fromkeys(magnets):
        mg = mg.replace('&amp;', '&')  # HTML 实体转义还原
        q = urllib.parse.urlparse(mg).query
        fn = urllib.parse.unquote_plus((urllib.parse.parse_qs(q).get('filename') or [''])[0])
        m = EP_RE.search(fn) or EP_RE.search(mg)
        if not m:
            continue
        key = f'E{int(m.group(2)):02d}'
        if key not in have:
            new.append((f'S{int(m.group(1)):02d}', key, mg))
    if not new:
        print(f'📺 {show}: 页面上没有比清单更新的集 (已知{len(have)}集)')
        return
    print(f'🆕 {show} 发现 {len(new)} 集新资源:')
    for season, key, mg in new:
        print(f'  {season}{key}  {mg[:80]}...')
        if auto:
            add(mg, f'{show} {season}')
    if not auto:
        print('(确认后加 --auto 直接入队, 或手动逐条 add)')


# ────────────────────────── CLI ──────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    cmd = args[0] if args else ''
    if cmd == 'search':
        print('电视剧搜索流程(agent 会话内完成, 本脚本负责选版计算):')
        print('  1. 多站点: knaben.org(需代理, 见 nas-media-download.md) / 剧集站(如被CF挡换源)')
        print('     关键词: "<剧名> S01 1080p x265" / "<英文名> Season 1 pack WEB-DL"')
        print('  2. 整理 versions.json: [{name,size_gb,seeders,kind:pack|split,quality_rank,episodes}]')
        print('     quality_rank: 0=WEB-DL/1080p 达标即 0, REMUX/4K=-1(更高), 720p=1')
        print('  3. python tv_pipeline.py pick versions.json  → 按输出建议 add')
    elif cmd == 'pick':
        pick(args[1])
    elif cmd == 'add':
        ep = None
        if '--ep' in args:
            i = args.index('--ep'); ep = args[i + 1]; args = args[:i] + args[i + 2:]
        add(args[1], args[2], ep)
    elif cmd == 'progress':
        progress(args[1])
    elif cmd == 'verify':
        ok = verify(args[1], args[2])
        sys.exit(0 if ok else 2)
    elif cmd == 'scan':
        season = args[args.index('--season') + 1] if '--season' in args else None
        expect = int(args[args.index('--expect') + 1]) if '--expect' in args else None
        scan(args[1], season, expect)
    elif cmd == 'follow':
        follow(args[1], args[2], '--auto' in args)
    else:
        print(__doc__)
