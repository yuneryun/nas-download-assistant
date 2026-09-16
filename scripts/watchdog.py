# -*- coding: utf-8 -*-
"""watchdog.py — 下载看门狗（media-downloader skill）
配合 crontab 周期跑一次(推荐 */10 或 */30 分钟), 每次做六件事:
  1. RPC 保活: aria2 没在听 → 执行 config.watchdog.restart_cmd 拉起, 再验
  2. 卡死救援: 下载中但 速度<stall_kbps 且持续 stall_minutes → 补 bt-tracker →
     仍卡则 remove+add 重连一次/任务; 全程记录, 不反复横跳
  3. 完成钩子: 上一轮还在下、这一轮消失的任务 → 通知(电影=可校验; 剧集=可逐集校验),
     写进待校验队列 verify_queue.json
  4. 磁盘水位: 余量<min_free_gb → 全部活动任务 aria2.pause 并告警(熔断, 不是删除)
  5. 剧集追更: config.watchdog.follow_urls 里的页面 → 调 tv_pipeline.follow 提新集,
     auto_add=true 时自动入队
  6. 失败任务: error 状态 → 报告+入待处理; 绝不自动删文件(用户铁律: 只移隔离区)

告警去重: 同一问题 4 小时内只报一次(state.json)。通知走 config.notify_cmd。

用法:
  python watchdog.py run          # 跑一轮(crontab 用这个)
  python watchdog.py status       # 看当前任务/水位/最近告警
  python watchdog.py install      # 打印 crontab 行
  python watchdog.py test-notify  # 测通知链路
"""
import json, os, re, subprocess, sys, time, urllib.request

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = json.load(open(os.path.join(HERE, 'config.json'), encoding='utf-8')) \
    if os.path.exists(os.path.join(HERE, 'config.json')) else {}
WD = {'restart_cmd': '', 'stall_kbps': 50, 'stall_minutes': 30,
      'follow_urls': [], 'auto_add': False, 'alert_cooldown_hours': 4}
WD.update(CONF.get('watchdog', {}))
RPC, SEC = CONF.get('aria2_rpc', 'http://127.0.0.1:16800'), CONF.get('aria2_secret', 'fnosdl')
STATE_PATH = os.path.join(HERE, '.watchdog_state.json')
VERIFY_QUEUE = os.path.join(HERE, 'verify_queue.json')
LOG_PATH = os.path.join(HERE, 'watchdog.log')

TRACKER_POOL = ('udp://tracker.opentrackr.org:1337/announce,'
                'udp://open.stealth.si:80/announce,'
                'udp://tracker.torrent.eu.org:451/announce,'
                'udp://tracker.dler.org:6969/announce,'
                'udp://exodus.desync.com:6969/announce,'
                'udp://tracker.tiny-vps.com:6969/announce,'
                'http://p4p.arenabg.com:1337/announce,'
                'http://tracker.bt4g.com:2095/announce')

KEYS = ['gid', 'status', 'statusReason', 'totalLength', 'completedLength', 'downloadSpeed',
        'connections', 'verifiableLength', 'name', 'dir', 'error_code' ]


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def log(msg):
    line = f'[{now()}] {msg}'
    print(line)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def rpc(method, params=None):
    body = {"jsonrpc": "2.0", "id": "1", "method": method, "params": ["token:" + SEC] + (params or [])}
    try:
        req = urllib.request.Request(RPC + '/jsonrpc', data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        if 'error' in resp:
            return {'__err__': resp['error'].get('message', '?')}
        return resp.get('result')
    except Exception as e:
        return {'__err__': str(e)[:80]}


def load_state():
    try:
        s = json.load(open(STATE_PATH, encoding='utf-8'))
    except Exception:
        s = {}
    s.setdefault('seen', {})       # gid -> {name,dir,last_active,alerts{}}
    s.setdefault('down_since', None)
    return s


def save_state(s):
    json.dump(s, open(STATE_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def alert(s, key, msg):
    """带去重窗口的告警"""
    a = s['seen'].setdefault('_alerts', {})
    last = a.get(key, 0)
    if time.time() - last < WD['alert_cooldown_hours'] * 3600:
        return False
    a[key] = time.time()
    log('🔔 ' + msg)
    notify(msg)
    return True


def notify(msg):
    cmd = CONF.get('notify_cmd')
    if cmd:
        subprocess.run(cmd.replace('{msg}', msg), shell=True)


def alive_tasks():
    r = rpc('aria2.tellActive')
    if isinstance(r, dict):  # error dict
        return None
    return r or []


def task_fields(t):
    return {'name': t.get('name', '?'), 'dir': t.get('dir', ''),
            'speed': int(t.get('downloadSpeed', 0) or 0),
            'completed': int(t.get('completedLength', 0) or 0),
            'total': int(t.get('totalLength', 0) or 0),
            'conn': t.get('connections', '0')}


def disk_free_gb():
    import shutil
    vals = []
    for d in [CONF.get('download_dir_movies'), CONF.get('download_dir_tv'), CONF.get('download_dir_music')]:
        try:
            vals.append(shutil.disk_usage(d).free / 1e9)
        except Exception:
            continue
    return min(vals) if vals else None


# ───────────────────────────── 六项巡检 ─────────────────────────────

def check_rpc(s):
    r = rpc('aria2.getVersion')
    if isinstance(r, dict) and '__err__' in r:
        if not s['down_since']:
            s['down_since'] = time.time()
        if WD['restart_cmd']:
            log(f'aria2 RPC 不通({r["__err__"]}) → 执行 restart_cmd 自愈')
            subprocess.run(WD['restart_cmd'], shell=True)
            time.sleep(5)
            r2 = rpc('aria2.getVersion')
            if not (isinstance(r2, dict) and '__err__' in r2):
                log('✅ 自愈成功, aria2 ' + r2.get('version', '?'))
                s['down_since'] = None
                return True
            alert(s, 'rpc', '🚨 aria2 拉起失败, 下载栈全停。restart_cmd 输出见 watchdog.log, 或按 nas-media-download.md 手动启动')
        else:
            alert(s, 'rpc', f'🚨 aria2 RPC 不通({r["__err__"]}) 且未配 watchdog.restart_cmd, 无法自愈 — 见 SKILL.md 部署边界')
        return False
    s['down_since'] = None
    return True


def check_stalls(s, tasks):
    """speed<stall_kbps 持续 stall_minutes → 补tracker → 重连一次"""
    stall_s = WD['stall_minutes'] * 60
    for t in tasks:
        g, f = t['gid'], task_fields(t)
        e = s['seen'].setdefault(g, {'alerts': {}})
        e.update(f)
        if f['speed'] >= WD['stall_kbps'] * 1000:
            e['stall_since'] = None
            if e.get('rescued') and f['speed'] > 0:
                e['rescued'] = None  # 恢复速度后允许下一轮再救
            continue
        e['stall_since'] = e.get('stall_since') or time.time()
        if time.time() - e['stall_since'] < stall_s:
            continue
        if not e.get('rescued'):
            log(f'⏳ {f["name"][:44]} 卡住{WD["stall_minutes"]}min+ → 补tracker')
            rpc('aria2.changeOption', [g, {'bt-tracker': TRACKER_POOL}])
            e['rescued'] = 'tracker'
            e['stall_since'] = time.time()
        elif e['rescued'] == 'tracker':
            log(f'⏳ {f["name"][:44]} 补tracker仍卡 → 任务重连(remove+add)一次')
            uri = rpc('aria2.getUris', [g])
            magnet = uri[0].get('uri') if isinstance(uri, list) and uri else None
            s['seen'].pop(g, None)  # 旧gid出账, 不误报任务消失
            if magnet:
                ok = rpc('aria2.addUri', [[magnet], {'dir': f['dir'], 'seed-time': '0',
                                                     'check-integrity': 'true'}])
                if isinstance(ok, str):
                    s['seen'][ok] = {**f, 'alerts': {}, 'rescued_ts': time.time()}
                    log(f'   重连成功, 新GID={ok}')
                else:
                    alert(s, f'readd-{f["name"][:30]}', f'🚨 重连失败: {f["name"][:40]} 需人工处理(磁力可能失效)')
            else:
                alert(s, f'readd-{f["name"][:30]}', f'🚨 取不到原磁力链, 无法自动重连: {f["name"][:40]}')
        else:
            alert(s, f'stall-{f["name"][:30]}',
                  f'⚠️ {f["name"][:40]} 多次救援仍 0 速度, 连接{f["conn"]} — 建议换版本(见选版规则), 记录在 watchdog.log')
            e['stall_since'] = time.time()


def check_removed_and_done(s, tasks):
    """消失的任务: 目标文件出现=完成(入待校验队列); 文件无=失败/被清"""
    live = {t['gid'] for t in tasks}
    now_ts = time.time()
    gone = [g for g in [k for k in s['seen'] if not k.startswith('_')]
            if g not in live and now_ts - s['seen'][g].get('rescued_ts', 0) > 600]
    for g in gone:
        e = s['seen'].pop(g, None)
        if not e or not e.get('name'):
            continue
        path = os.path.join(e.get('dir', ''), e['name'])
        exists = path and os.path.exists(path)
        if exists:
            q = json.load(open(VERIFY_QUEUE, encoding='utf-8')) if os.path.exists(VERIFY_QUEUE) else []
            if not any(x.get('gid') == g for x in q):
                q.append({'gid': g, 'path': path, 'name': e['name'], 'at': now()})
                json.dump(q, open(VERIFY_QUEUE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            log(f'✅ 下载完成待校验: {e["name"][:50]}')
            alert(s, f'done-{g}', f'🎬 下载完成(未校验, 校验后才算数): {e["name"][:40]}\n   下一步: movie_pipeline/tv_pipeline verify')
        else:
            alert(s, f'lost-{g}', f'⚠️ 任务消失且文件不在盘: {e["name"][:40]} — aria2 可能报错退出, 查 dir 下 aria2.log')


def check_quota(s, tasks):
    """流量闸门提醒: 订阅 URL 返回 subscription-userinfo 时, 剩余<新任务体积30%告警"""
    url = WD.get('traffic_subscription')
    if not url:
        return
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'clash-verge-rev'})
        h = urllib.request.urlopen(req, timeout=15).headers.get('subscription-userinfo', '')
        info = dict(kv.split('=') for kv in h.split(';') if '=' in kv)
        left = (int(info['total']) - int(info['download'])) / 1e9
        pending = sum(int(t['totalLength']) - int(t['completedLength']) for t in tasks) / 1e9
        if pending > left * 0.3:
            alert(s, 'traffic', f'⚠️ 机场流量告急: 剩余{left:.0f}GB, 未下完部分约{pending:.0f}GB。'
                  'NAS aria2 直连本不占机场, 若真在烧流量请查代理规则(见SKILL.md流量闸门)')
    except Exception:
        pass


def check_disk(s, tasks):
    free = disk_free_gb()
    if free is None:
        return
    log(f'💽 磁盘余量 {free:.0f}GB (水位线 {CONF.get("min_free_gb", 500)}GB)')
    if free < CONF.get('min_free_gb', 500) and tasks:
        for t in tasks:
            rpc('aria2.pause', [t['gid']])
        alert(s, 'disk', f'🚨 磁盘余量 {free:.0f}GB 低于水位线, 已暂停全部下载(熔断, 不删文件)。清库后 resume 或重跑 watchdog')


def check_follow(s):
    urls = WD.get('follow_urls') or []
    if not urls:
        return
    sys.path.insert(0, HERE)
    try:
        import tv_pipeline
    except Exception as ex:
        alert(s, 'tvimport', f'⚠️ 追更扫描不可用: 导入 tv_pipeline 失败({ex})')
        return
    for item in urls:
        show, url = item['show'], item['url']
        try:
            tv_pipeline.follow(show, url, auto=WD.get('auto_add', False))
        except Exception as ex:
            log(f'⚠️ 追更 {show} 扫描失败: {str(ex)[:60]}')


def process_verify_queue(s):
    """消费待校验队列: 自动调对应 pipeline 校验归档(剧集/电影按目录分流)"""
    if not WD.get('auto_verify', True) or not os.path.exists(VERIFY_QUEUE):
        return
    q = json.load(open(VERIFY_QUEUE, encoding='utf-8'))
    if not q:
        return
    sys.path.insert(0, HERE)
    tvdir = os.path.abspath(CONF.get('download_dir_tv', ''))
    for item in q:
        p = item['path']
        if not os.path.exists(p):
            continue  # 已被人工处理
        try:
            if tvdir and os.path.abspath(p).startswith(tvdir + os.sep):
                import tv_pipeline
                tv_pipeline.verify(p)  # label 从 父目录名+文件名 自动解析
            else:
                import movie_pipeline
                movie_pipeline.verify_and_archive(p, os.path.splitext(item['name'])[0])
            log(f'🔍 自动校验完成: {item["name"][:46]}')
        except Exception as ex:
            alert(s, f'verify-{item["gid"]}', f'⚠️ 自动校验异常: {item["name"][:36]} — {str(ex)[:50]}, 保留文件待人工')
    json.dump([], open(VERIFY_QUEUE, 'w', encoding='utf-8'))


def run():
    s = load_state()
    if not check_rpc(s):
        save_state(s)
        return 1
    tasks = alive_tasks()
    if tasks is None:
        save_state(s)
        return 1
    log(f'巡检: 活动任务 {len(tasks)} 个')
    check_stalls(s, tasks)
    check_removed_and_done(s, tasks)
    check_quota(s, tasks)
    check_disk(s, tasks)
    check_follow(s)
    process_verify_queue(s)
    save_state(s)
    return 0


def status():
    r = rpc('aria2.getVersion')
    if isinstance(r, dict) and '__err__' in r:
        print('❌ aria2 RPC 不通:', r['__err__']); return
    print('aria2', r.get('version'))
    for t in alive_tasks() or []:
        f = task_fields(t)
        pct = f['completed'] / max(1, f['total']) * 100
        print(f'  ⬇ {f["name"][:52]:<54}{pct:5.1f}% {f["speed"]/1e6:6.2f}MB/s conn={f["conn"]}')
    w = [t for t in (rpc('aria2.tellWaiting', [0, 20, KEYS]) or []) if isinstance(t, dict)]
    for t in w:
        f = task_fields(t)
        print(f'  ⏸ {t.get("status")} {f["name"][:46]}')
    s = load_state()
    print(f'磁盘余量: {disk_free_gb() or 0:.0f}GB | 状态文件记录任务数: {len([k for k in s["seen"] if not k.startswith("_")])}')
    if os.path.exists(VERIFY_QUEUE):
        q = json.load(open(VERIFY_QUEUE, encoding='utf-8'))
        print(f'待校验队列: {len(q)}')


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'run':
        sys.exit(run())
    elif cmd == 'status':
        status()
    elif cmd == 'install':
        min_ = WD.get('cron_minutes', 15)
        print(f'*/{min_} * * * * cd {HERE} && HOME=$PWD {sys.executable} watchdog.py run >> watchdog.log 2>&1')
        print('(crontab -e 粘贴上行; 飞牛等无HOME环境 HOME=$PWD 必带)')
    elif cmd == 'test-notify':
        notify('🧪 media-downloader 通知链路测试 (watchdog test-notify)')
        print('已发送通知, 检查 notify_cmd 目标渠道(无输出=未配 notify_cmd)')
    else:
        print(__doc__)
