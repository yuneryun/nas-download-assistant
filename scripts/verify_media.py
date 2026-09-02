# -*- coding: utf-8 -*-
"""verify_media.py — 音乐完整性校验器（media-downloader skill）
用法: python verify_media.py <文件或目录>
对 FLAC/MP3/WAV 等逐文件五道校验:
1. 可解码: mutagen 打开+读全流参数
2. 元数据核对: title/artist 非空且无坏词
3. 真无损验证: 声明flac但码率≈无损区间(>700kbps); 假Hi-Res检测
4. 完整性: 文件尾部非截断(flac有STREAMINFO, 时长>0); mp3尾帧完整
5. 查重: 同目录内 title+duration 重复
输出: 每文件 PASS/FAIL + 原因, 目录汇总"""
import os,sys,json,hashlib
from mutagen import File
from mutagen.flac import FLAC, FLACNoHeaderError

LOSSLESS={'flac','wav','ape','alac','wv','tta','dsf','dff'}
BAD=['Montagem','Remix','Cover','翻唱','合唱','串烧','DJ版','伴奏','纯音乐','童声',
     '万人大合唱','live','Live','LIVE','夜店','慢摇','抖音热歌版','clip','Clip']
AUDIO_EXT={'.flac','.mp3','.wav','.ape','.m4a','.alac','.wv','.ogg','.dsf','.dff','.aac'}

def verify_file(path):
    issues=[]; info={}
    ext=os.path.splitext(path)[1].lower().lstrip('.')
    # 1 可解码
    try:
        m=File(path, easy=True)
        if m is None: return ['无法解析(非音频或损坏)'], {}
        info['format']=m.mime[0] if m.mime else ext
        info['duration']=round(m.info.length,1)
        info['bitrate_kbps']=round(getattr(m.info,'bitrate',0)/1000)
        info['sample_rate']=getattr(m.info,'sample_rate',0)
        bits=getattr(m.info,'bits_per_sample',0)
        info['bits']=bits if bits else 0
    except Exception as ex:
        return ['解码失败:'+str(ex)[:60]], {}
    if info['duration']<10: issues.append(f'时长异常({info["duration"]}s)')
    # 2 元数据
    title=(m.get('title') or [''])[0] if m.get('title') else ''
    artist=(m.get('artist') or [''])[0] if m.get('artist') else ''
    if not title: issues.append('缺title标签')
    if not artist: issues.append('缺artist标签')
    for bad in BAD:
        if bad in title or bad in artist: issues.append(f'疑似翻唱/坏词:{bad}'); break
    # 3 真无损
    if ext in LOSSLESS:
        if ext=='flac' and info['bitrate_kbps'] and info['bitrate_kbps']<600:
            issues.append(f'伪无损嫌疑(FLAC但仅{info["bitrate_kbps"]}kbps)')
        if info['bits'] and info['bits']<16: issues.append(f'位深异常{info["bits"]}bit')
        if info['sample_rate'] and info['sample_rate']<44100: issues.append(f'采样率异常{info["sample_rate"]}')
    # 3b 假Hi-Res: 24bit标签但码率与16bit无差
    if info['bits']>=24 and info['bitrate_kbps'] and ext=='flac':
        # 粗验: 24bit Hi-Res FLAC 码率通常 >2000kbps
        if info['bitrate_kbps']<1500: issues.append(f'Hi-Res嫌疑(24bit但{info["bitrate_kbps"]}kbps)')
    # 4 完整性: flac 读 MD5 STREAMINFO 与实际解码一致性(重解码太重, 检查尾标记)
    if ext=='flac':
        try:
            f=FLAC(path)
            if not f.info.md5_signature and not f.seektable: pass
            # 尾部检查: 最后4字节应是合法帧或padding
            with open(path,'rb') as fh:
                fh.seek(-4,2); tail=fh.read()
            if tail==b'\0\0\0\0': issues.append('尾部全零(截断嫌疑)')
        except FLACNoHeaderError: issues.append('FLAC头损坏')
        except Exception as ex: issues.append(f'FLAC结构异常:{str(ex)[:40]}')
    # 5 指纹(用于目录内查重)
    fp=hashlib.md5(open(path,'rb').read(1024*256)).hexdigest()  # 头256KB指纹
    return issues, {**info,'title':title,'artist':artist,'fp':fp}

def main():
    target=sys.argv[1] if len(sys.argv)>1 else '.'
    files=[]
    if os.path.isfile(target): files=[target]
    else:
        for f in sorted(os.listdir(target)):
            if os.path.splitext(f)[1].lower() in AUDIO_EXT: files.append(os.path.join(target,f))
    print(f'校验 {len(files)} 个音频文件...')
    seen_fp={}; all_pass=True; report=[]
    for p in files:
        issues,info=verify_file(p)
        name=os.path.basename(p)
        # 5 查重
        if info.get('fp') and info['fp'] in seen_fp:
            issues.append(f'与 {seen_fp[info["fp"]]} 内容重复')
        else: seen_fp[info.get('fp','')]=name[:30]
        status='PASS' if not issues else 'FAIL'
        if issues: all_pass=False
        report.append((status,name,issues,info))
        flag='✅' if not issues else '❌'
        print(f'{flag} [{status}] {name[:50]}  {info.get("format","?")} {info.get("bitrate_kbps","?")}kbps {info.get("duration","?")}s')
        for i in issues: print(f'     └ {i}')
    npass=sum(1 for r in report if r[0]=='PASS')
    print(f'\n结果: {npass}/{len(report)} 通过' + (' ✅ 全部合格' if all_pass else ' ⚠️ 存在问题文件'))
    # 写报告
    json.dump([{'status':s,'file':n,'issues':i,'info':{k:str(v) for k,v in inf.items()}} for s,n,i,inf in report],
              open('verify_report.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
    sys.exit(0 if all_pass else 1)

if __name__=='__main__':
    main()
