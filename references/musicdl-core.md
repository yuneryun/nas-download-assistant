# 音乐下载核心坑库（配合 media-downloader 主文档使用）

## musicdl 安装
- Windows: `C:\Users\35342\Desktop\musicdl-master\.venv\Scripts\musicdl.exe` (2.13.6)
- Linux/NAS: `pip install musicdl mutagen` (2.13.8, API 相同)
- 入口是 console_script `musicdl`；`python -m musicdl.musicdl` 会崩

## Python API (实测)
```python
from musicdl.musicdl import MusicClient   # 不是 from musicdl import musicdl
client = MusicClient()
results = client.search('歌手 歌名')       # 只收 keyword; 返回 {源名: [SongInfo,...]}
client.download([song_info])               # 必须传 LIST
song_info.work_dir = tmpdir                # 文件名 = song_name - identifier.ext
```
SongInfo: `song_name`(非songname) / `singers`(str) / `ext`(带点'.flac') / `file_size_bytes` / `source`

## 四大坑
1. **下A得B**: QQ 源 ID 映射错位, 下《青花瓷》得《晴天》→ 搜索结果不可信, 下载后 mutagen 读元数据校验
2. **翻唱污染**: 按 singers 字段过滤 + 坏词黑名单 ['Remix','Cover','翻唱','Live','伴奏','DJ','童声','Montagem',...]
3. **版权缺失**: 部分经典全网无原版, FAIL 后换歌补数, 不强下
4. **残留垃圾**: 每次搜索生成 search_results.pkl + 时间戳空目录, 定期清理

## 音质保底策略（用户规则：保证能下到）

- 候选选择顺序：**歌手匹配无损 → 匹配的高码率有损(320k) → 匹配的任意有损 → 跨源再扫一轮**
- 无损没有时**绝不放弃**，取最高码率有损交付，并在清单标注实际音质（如 `128k MP3`）
- 与"翻唱过滤"的边界：**音质可以降级，内容不能妥协**——翻唱/串烧仍然拒收，宁可 FAIL 换源
- 极端情况（全网无原版，如部分华语老歌）：记入 manifest 缺失清单，不硬凑

## 批量下载姿势
- 逐候选重试: 优先级=歌手匹配无损>匹配有损>不匹配无损, 最多试前6个
- 断点续跑: 目标文件 `{artist} - {song}.*` 存在则 SKIP, manifest.csv 逐行 flush
- 无损优先排序: `sorted(songs, key=lambda s:(0 if s.ext.lstrip('.') in LOSSLESS else 1, -size))`
- LOSSLESS={'flac','wav','ape','alac','wv','tta','dsf','dff'}

## 交付校验
- mutagen 读 bitrate/sample_rate/bits 确认真无损 (16bit/44.1kHz=CD级, 24bit/192kHz=Hi-Res)
- 与目标清单对比, 最终目录只留 `歌手 - 歌名.ext`

## NAS/远程执行
- platformdirs 在家目录无效时崩(飞牛OS /home/Hermes 不存在) → 先 `export HOME=<可写目录>`
- 自包含单文件脚本交接: 只依赖 pip install musicdl mutagen, 输出到脚本同目录, 结尾打印汇总
- 模板: C:\Users\35342\Desktop\huayu_rock_dl.py (118首实战校验过)
