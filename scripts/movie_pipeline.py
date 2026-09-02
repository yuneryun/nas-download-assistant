# -*- coding: utf-8 -*-
"""movie_pipeline.py — 电影下载一体化: 搜索→选版→下载→校验→归档
用法:
  python movie_pipeline.py search <片名>        # 搜索并输出版本对比表
  python movie_pipeline.py download <磁力链> "<归档名>"   # 下+校验+归档
  python movie_pipeline.py status               # 当前下载状态
  python movie_pipeline.py verify <文件路径> "<片名> (年份)"  # 单独校验+归档
依赖: aria2c(RPC), ffprobe/ffmpeg; 配置见 config.json
"""
import json,os,re,subprocess,sys,urllib.request,shutil,time

HERE=os.path.dirname(os.path.abspath(__file__))
CONF_PATH=os.path.join(HERE,'config.json')
CONF=json.load(open(CONF_PATH,encoding='utf-8')) if os.path.exists(CONF_PATH) else {
    'download_dir_movies':'/vol2/1000/影视/电影','min_free_gb':500,
    'aria2_rpc':'http://127.0.0.1:16800','aria2_secret':'fnosdl',
    'library_index':os.path.expanduser('~/media_library.json'),
    'notify_cmd':''}
RPC=CONF['aria2_rpc']; SEC=CONF['aria2_secret']
LIB=CONF.get('library_index')

def rpc(method, params=None):
    body={"jsonrpc":"2.0","id":"1","method":method,"params":["token:"+SEC]+(params or [])}
    return json.loads(urllib.request.urlopen(urllib.request.Request(
        RPC+'/jsonrpc',data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json'}),timeout=15).read()).get('result')

def disk_free_gb(path):
    u=shutil.disk_usage(path); return u.free/1e9

def check_duplicate(title):
    if LIB and os.path.exists(LIB):
        lib=json.load(open(LIB,encoding='utf-8'))
        return [e for e in lib if title.lower() in e.get('title','').lower()]
    return []

def version_table(versions):
    """versions: [{name,res,size_gb,seeders,magnet,quality_rank}] → 排序+建议列"""
    for v in versions: 
        v['speed_class']='⚡最快' if v['seeders']>=50 else ('⚖平衡' if v['seeders']>=20 else '🐢慢')
    versions.sort(key=lambda v:(v['quality_rank'],-v['seeders']))
    print(f"{'版本':<52}{'体积':>9}{'做种':>5}  建议")
    for v in versions:
        rec='⭐画质最佳' if v['quality_rank']==0 else ''
        print(f"{v['name']:<52}{v['size_gb']:>7.1f}GB{v['seeders']:>5}  {v['speed_class']} {rec}")
    return versions

def eta_hours(size_gb, seeders):
    """粗估: 每做种平均贡献 ~0.5MB/s, 封顶带宽~25MB/s"""
    est_speed=min(25, max(0.3, seeders*0.5))  # MB/s
    return size_gb*1024/est_speed/3600

def pick_version(versions):
    """用户规则: 画质/体积优先可牺牲速度; 做种>=30最高画质直接下; ETA>24h才降级"""
    versions.sort(key=lambda v:(v['quality_rank'],-v['seeders']))
    for v in versions:
        eta=eta_hours(v['size_gb'],v['seeders'])
        if v['seeders']>=30 and eta<24:
            return v,'做种≥30且ETA<24h, 按规则直取最高画质'
        if v['seeders']>=10 and eta<24:
            return v,'ETA<24h, 可接受'
    # 全部>24h → 降级
    fast=sorted(versions,key=lambda v:-v['seeders'])[0]
    return fast,'所有高画质版ETA>24h, 按规则降级到最快版本'

def start_download(magnet,dst):
    if disk_free_gb(dst) < CONF['min_free_gb']:
        print(f'❌ 磁盘余量不足{CONF["min_free_gb"]}GB, 拒绝下载'); sys.exit(1)
    os.makedirs(dst,exist_ok=True)
    log=os.path.join(dst,'aria2.log')
    cmd=(f'setsid aria2c --seed-time=0 --enable-rpc --rpc-listen-port=16800 '
         f'--rpc-secret={SEC} --dir={dst} --file-allocation=none '
         f'--listen-port=26999 --dht-listen-port=26998 "{magnet}" </dev/null >>{log} 2>&1 &')
    subprocess.run(cmd,shell=True)
    time.sleep(3)
    active=rpc('aria2.tellActive')
    if active:
        print('✅ 下载已启动 GID:',active[0]['gid'])
    else:
        print('⚠️ 未立即见到活动任务, 查日志:',log)

def verify_and_archive(raw_path,archive_name):
    """四道校验 + 改名归档 + 字幕提示 + 写库"""
    issues=[]
    # 1 元数据
    r=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
        'stream=width,height,codec_name,bit_rate','-show_entries','format=duration',
        '-of','json',raw_path],capture_output=True,text=True)
    meta=json.loads(r.stdout or '{}')
    try:
        st=meta['streams'][0]; w,h=st['width'],st['height']
        dur=float(meta['format']['duration'])/60
        br=int(st.get('bit_rate') or meta['format'].get('bit_rate') or 0)
        print(f'分辨率:{w}x{h} 编码:{st["codec_name"]} 码率:{br/1e6:.1f}Mbps 时长:{dur:.0f}min')
        if (w,h)!=(3840,2160) and '2160' not in raw_path: issues.append(f'非4K({w}x{h})')
        if br and br<20e6 and h==2160: issues.append('4K但码率<20Mbps(假4K嫌疑)')
    except Exception as e: issues.append(f'元数据读取失败:{e}')
    # 2 解码实测
    d=int(float(meta.get('format',{}).get('duration',0)))
    for pos in (60, d//2, max(0,d-90)):
        r=subprocess.run(['ffmpeg','-v','error','-ss',str(pos),'-i',raw_path,'-t','30','-f','null','-'],
                         capture_output=True,text=True)
        if r.stderr.strip(): issues.append(f'解码错误@{pos}s: {r.stderr[:80]}')
    # 3/4 哈希+容器已由 aria2 完成校验 + ffprobe 无 Invalid data
    r=subprocess.run(['ffprobe','-v','error',raw_path],capture_output=True,text=True)
    if 'Invalid data' in r.stderr: issues.append('容器损坏')
    if issues:
        print('❌ 校验未通过:',issues); return False
    # 归档
    dst=os.path.join(CONF['download_dir_movies'],archive_name)
    dst_full=dst+'.mkv' if not raw_path.endswith('.mkv') else dst
    os.makedirs(os.path.dirname(dst_full),exist_ok=True)
    shutil.move(raw_path,dst_full)
    # 写库
    if LIB:
        lib=json.load(open(LIB,encoding='utf-8')) if os.path.exists(LIB) else []
        lib.append({'title':archive_name,'path':dst_full,
                    'size_gb':round(os.path.getsize(dst_full)/1e9,1),
                    'verified':True,'date':time.strftime('%Y-%m-%d')})
        json.dump(lib,open(LIB,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    # 通知
    if CONF.get('notify_cmd'):
        subprocess.run(CONF['notify_cmd'].replace('{msg}',
            f'🎬 {archive_name} 校验完成, 可观看 ({dst_full})'),shell=True)
    print(f'✅ 校验通过并归档: {dst_full}')
    # 字幕提醒
    print('ℹ️ 字幕: 请到 OpenSubtitles/SubHD 搜索中文字幕, 与视频同名放同目录')
    return True

if __name__=='__main__':
    cmd=sys.argv[1] if len(sys.argv)>1 else 'status'
    if cmd=='search':
        print('搜索流程: 用 web_search 搜 "<片名> 4K REMUX 磁力链", 整理出 versions 列表后调 version_table()+pick_version()')
        print('（搜索需联网多站点对比, 由 agent 会话内完成, 本脚本负责选版计算）')
    elif cmd=='download':
        magnet,archive=sys.argv[2],sys.argv[3]
        dup=check_duplicate(archive)
        if dup: print('⚠️ 库里已有:',dup); sys.exit(0)
        start_download(magnet,CONF['download_dir_movies'])
    elif cmd=='status':
        for t in (rpc('aria2.tellActive') or []):
            print(t.get('gid'),'%.1f%%'%(int(t['completedLength'])/max(1,int(t['totalLength']))*100),
                  int(t['downloadSpeed'])/1e6,'MB/s')
        if not rpc('aria2.tellActive'): print('无活动任务')
    elif cmd=='verify':
        verify_and_archive(sys.argv[2],sys.argv[3])
    else:
        print(__doc__)
