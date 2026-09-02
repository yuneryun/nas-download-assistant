# -*- coding: utf-8 -*-
"""media-downloader selftest: 部署自检脚本
检查: aria2 RPC / ffprobe / 磁盘水位 / 目录可写 / (可选)试下小文件"""
import json,os,sys,subprocess,urllib.request,shutil

CONF=json.load(open(os.path.join(os.path.dirname(__file__),'config.json'),encoding='utf-8')) \
    if os.path.exists(os.path.join(os.path.dirname(__file__),'config.json')) else {
        'download_dir_movies':'/vol2/1000/影视/电影',
        'download_dir_music':'/vol2/1000/音乐',
        'aria2_rpc':'http://127.0.0.1:16800','aria2_secret':'fnosdl',
        'min_free_gb':500}
ok=lambda m:print('  ✅',m); bad=lambda m:print('  ❌',m)
res=[]
print('== media-downloader 自检 ==')

# 1 ffprobe
r=subprocess.run(['ffprobe','-version'],capture_output=True,text=True)
res.append(('ffprobe', r.returncode==0))

# 2 磁盘
d=CONF['download_dir_movies']
if os.path.exists(d):
    usage=shutil.disk_usage(d)
    free_gb=usage.free/1e9
    res.append((f'磁盘余量 {free_gb:.0f}GB (水位{CONF["min_free_gb"]}GB)', free_gb>CONF['min_free_gb']))
    # 3 可写
    try:
        t=os.path.join(d,'.selftest'); open(t,'w').write('x'); os.remove(t)
        res.append((f'{d} 可写', True))
    except Exception as e:
        res.append((f'{d} 可写: {e}', False))
else:
    res.append((f'目录不存在: {d}', False))

# 4 aria2 RPC
try:
    req=urllib.request.Request(CONF['aria2_rpc']+'/jsonrpc',
        data=json.dumps({"jsonrpc":"2.0","id":"1","method":"aria2.getVersion",
                         "params":["token:"+CONF['aria2_secret']]}).encode(),
        headers={'Content-Type':'application/json'})
    v=json.loads(urllib.request.urlopen(req,timeout=8).read())
    res.append(('aria2 RPC '+v['result']['version'], True))
except Exception as e:
    res.append(('aria2 RPC: '+str(e)[:60], False))

# 5 musicdl/mutagen (音乐部分, 可选)
try:
    import mutagen; res.append(('mutagen '+mutagen.version_string, True))
except Exception: res.append(('mutagen 未装(音乐功能不可用)', False))
try:
    import musicdl; res.append(('musicdl 已装', True))
except Exception: res.append(('musicdl 未装(音乐功能不可用)', False))

fails=0
for m,passed in res:
    (ok if passed else bad)(m); fails+=0 if passed else 1
print(f'\n结果: {len(res)-fails}/{len(res)} 通过' + (' — 可投入使用 ✅' if fails==0 else ' — 请修复上述❌'))
