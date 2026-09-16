# -*- coding: utf-8 -*-
"""library_refresh.py — 校验归档成功后通知媒体库服务器增量刷新 (Emby/Jellyfin/Plex)
零依赖(标准库 urllib)。在 config.json 配:
  "media_server": {
    "emby":     {"url": "http://192.168.1.3:8096", "key": "<Api-Master-Token>"},
    "jellyfin": {"url": "http://192.168.1.3:8096", "key": "<Api-Master-Token>"},
    "plex":     {"url": "http://192.168.1.3:32400", "token": "<X-Plex-Token>",
                 "sections": ["22"]}
  }
只填你实际部署的那一项; 全空=静默跳过(不报错)。
路径映射: NAS 内部路径与服务器看到的库路径不同时配 path_map, 如
  "path_map": ["/vol2/1000", "/mnt/nas"]   # [源前缀, 目标前缀]
"""
import json, os, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def _conf():
    try:
        c = json.load(open(os.path.join(HERE, 'config.json'), encoding='utf-8'))
    except Exception:
        c = {}
    return c.get('media_server', {}) or {}


def _map_path(path, ms):
    pm = ms.get('path_map') or []
    if len(pm) == 2 and path.startswith(pm[0]):
        return pm[1] + path[len(pm[0]):]
    return path


def _get(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout).status


def _post(url, headers=None, timeout=10):
    req = urllib.request.Request(url, method='POST', headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout).status


def refresh(path=None, verbose=True):
    """path 提供时按库分别尝试定向刷新; 无 path 或定向失败则全库扫描兜底"""
    ms = _conf()
    if not ms:
        return True  # 未配置 = 无需刷新, 不算失败
    ok_any = False
    for name in ('emby', 'jellyfin'):
        cfg = ms.get(name)
        if not cfg:
            continue
        base, key = cfg['url'].rstrip('/'), cfg.get('key', '')
        try:
            if path and name == 'emby':
                # Emby 支持定向刷新指定路径
                st = _get(f'{base}/emby/Users/MediaLibrary/Refresh?{urllib.parse.urlencode({"path": _map_path(path, ms), "api_key": key, "Recursive": "true"})}')
            elif path and name == 'jellyfin':
                # Jellyfin 定向刷新走 ScheduledTasks 里的 FullScan 太重, 直接触发库扫描
                st = _post(f'{base}/Library/Refresh?{urllib.parse.urlencode({"api_key": key})}')
            else:
                st = _post(f'{base}/Library/Refresh?{urllib.parse.urlencode({"api_key": key})}')
            ok_any = ok_any or st < 400
            if verbose:
                print(f'{"✅" if st < 400 else "⚠️"} {name} refresh -> HTTP {st}')
        except Exception as e:
            if verbose:
                print(f'⚠️ {name} refresh 失败: {str(e)[:60]}')
    cfg = ms.get('plex')
    if cfg:
        base, tok = cfg['url'].rstrip('/'), cfg.get('token', '')
        try:
            if path:
                st = _get(f'{base}/library/sections/{cfg.get("section", 1)}/refresh?{urllib.parse.urlencode({"path": _map_path(path, ms), "X-Plex-Token": tok})}')
            else:
                st = _get(f'{base}/library/refresh?{urllib.parse.urlencode({"X-Plex-Token": tok})}')
            ok_any = ok_any or st < 400
            if verbose:
                print(f'{"✅" if st < 400 else "⚠️"} plex refresh -> HTTP {st}')
        except Exception as e:
            if verbose:
                print(f'⚠️ plex refresh 失败: {str(e)[:60]}')
    return ok_any


if __name__ == '__main__':
    sys.path.insert(0, HERE)
    p = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(0 if refresh(p) else 1)
