# -*- coding: utf-8 -*-
"""
云水间 · 听音  ——  本地音乐播放器服务端
纯 Python 标准库实现，零第三方依赖。

功能：
  /                    前端页面
  /api/local           本地曲库（扫描 F:\\音乐）
  /api/refresh         重新扫描本地曲库
  /api/stream/local/<id>   本地音频流（支持 Range，可拖动进度）
  /api/cover/local/<id>    本地 MP3 内嵌封面
  /api/search?q=       联网搜索公开音源（网易云，优先无损）
  /api/url?id=&source= 解析远程播放地址
  /api/proxy?u=        远程音频流代理（解决防盗链与跨域，支持 Range）
  /api/lyric?id=       歌词
  /api/cover?u=        远程封面代理
"""
import os
import shutil
import re
import sys
import json
import time
import socket
import struct
import hashlib
import threading
import mimetypes
import urllib.request
import urllib.parse
import concurrent.futures
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ---------------------------------------------------------------- 基础配置
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
PORT_CANDIDATES = [8791, 8792, 8793, 8795, 8801]

# 用户数据统一放 %APPDATA%\云水间听音：
# 程序可以放在任何位置（Program Files / U 盘 / 只读目录），数据都不会丢
APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
DATA_DIR = os.path.join(APPDATA, "云水间听音")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
CACHE_FILE = os.path.join(DATA_DIR, "cache_library.json")

AUDIO_EXT = (".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".wma", ".ape", ".opus")


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_config(d):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        cfg = load_config()
        cfg.update(d)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        return True
    except Exception as e:
        print("[配置] 保存失败：%s" % e)
        return False


def _has_audio(path):
    try:
        names = os.listdir(path)
    except Exception:
        return False
    for fn in names[:300]:
        if fn.lower().endswith(AUDIO_EXT):
            return True
    return False


def detect_music_dir():
    """按「环境变量 → 配置文件 → 常见目录」的顺序找一个可用的音乐目录"""
    env = os.environ.get("YSJ_MUSIC_DIR") or os.environ.get("MUSIC_DIR")
    if env and os.path.isdir(env):
        return env

    cfg_dir = (load_config() or {}).get("musicDir")
    if cfg_dir and os.path.isdir(cfg_dir):
        return cfg_dir

    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Music"), os.path.join(home, "音乐"),
        os.path.join(home, "Desktop", "音乐"),
        "D:\\音乐", "E:\\音乐", "F:\\音乐", "C:\\音乐",
        os.path.join(home, "Downloads"),
    ]
    for c in candidates:
        if os.path.isdir(c) and _has_audio(c):
            return c

    # 都没找到就用系统「音乐」目录（不存在也无妨，首次扫描为空，界面可改）
    return os.path.join(home, "Music")


# 实际使用的目录（可通过 /api/config 修改）
MUSIC_DIR = detect_music_dir()

API_BASE = "https://music-api.gdstudio.xyz/api.php"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

META_FILE = os.path.join(DATA_DIR, "cache_meta.json")
FAV_FILE = os.path.join(DATA_DIR, "favorites.json")
COVER_DIR = os.path.join(DATA_DIR, "covers")
COVER_MAX_MB = 50          # 封面缓存上限，超出自动清理最旧的
META_BUSY = False
META_LOCK = threading.Lock()


def _migrate_data_dir():
    """把旧版放在程序目录里的数据搬到 %APPDATA%。
    注意：目标是空文件时也要搬，不能只看"文件是否存在"，
    否则之前测试留下的空 favorites.json 会挡住真实数据。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        print("[数据] 创建目录失败：%s" % e)
        return

    def _count(path, want_list):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if want_list and not isinstance(d, list):
                return -1
            if (not want_list) and not isinstance(d, dict):
                return -1
            return len(d)
        except Exception:
            return -1

    moved = []
    for name, want_list in (("favorites.json", True),
                            ("cache_meta.json", False),
                            ("cache_library.json", True)):
        src = os.path.join(HERE, name)
        dst = os.path.join(DATA_DIR, name)
        if not os.path.exists(src):
            continue
        a = _count(src, want_list)
        b = _count(dst, want_list) if os.path.exists(dst) else -1
        if a > 0 and a > b:
            try:
                shutil.copy2(src, dst)
                moved.append(name)
            except Exception:
                pass

    # 封面：把旧目录里缺的文件补进去
    old_cov = os.path.join(HERE, "covers")
    new_cov = os.path.join(DATA_DIR, "covers")
    if os.path.isdir(old_cov):
        try:
            os.makedirs(new_cov, exist_ok=True)
            n = 0
            for fn in os.listdir(old_cov):
                sp = os.path.join(old_cov, fn)
                dp = os.path.join(new_cov, fn)
                if os.path.isfile(sp) and not os.path.exists(dp):
                    try:
                        shutil.copy2(sp, dp)
                        n += 1
                    except Exception:
                        pass
            if n:
                moved.append("covers/(%d)" % n)
        except Exception:
            pass

    if moved:
        print("[数据] 已迁至 %s ：%s" % (DATA_DIR, "、".join(moved)))


_migrate_data_dir()

# pythonw 启动时没有控制台，stdout/stderr 为 None，print 会直接崩溃
if sys.stdout is None or sys.stderr is None:
    try:
        _logf = open(os.path.join(HERE, "run.log"), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _logf
        if sys.stderr is None:
            sys.stderr = _logf
    except Exception:
        pass

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------- ID3 / MP3
MPEG1_L3_BITRATE = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
MPEG2_L3_BITRATE = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
SAMPLE_RATES = {
    3: [44100, 48000, 32000],   # MPEG1
    2: [22050, 24000, 16000],   # MPEG2
    0: [11025, 12000, 8000],    # MPEG2.5
}


def _decode_text(raw, enc_hint):
    """ID3 文本解码：兼容 UTF-8 / UTF-16 / GBK / Latin-1"""
    candidates = []
    if enc_hint == 1:
        candidates = ["utf-16", "utf-8", "gbk"]
    elif enc_hint == 2:
        candidates = ["utf-16-be", "utf-16", "utf-8"]
    elif enc_hint == 3:
        candidates = ["utf-8", "gbk"]
    else:
        candidates = ["utf-8", "gbk", "latin-1"]
    for enc in candidates:
        try:
            txt = raw.decode(enc, errors="strict")
            return txt.strip("\x00").strip()
        except Exception:
            continue
    return raw.decode("latin-1", errors="replace").strip("\x00").strip()


def read_id3(path):
    """解析 ID3v2 标签：标题/歌手/专辑/年份/内嵌封面偏移"""
    info = {"title": "", "artist": "", "album": "", "year": "", "pic_off": None, "pic_len": 0}
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if len(head) < 10 or head[0:3] != b"ID3":
                return info, 0
            ver = head[3]
            size_b = head[6:10]
            tag_size = ((size_b[0] & 0x7F) << 21) | ((size_b[1] & 0x7F) << 14) | \
                       ((size_b[2] & 0x7F) << 7) | (size_b[3] & 0x7F)
            body_start = 10
            f.seek(body_start)
            body = f.read(tag_size)
            total = 10 + tag_size + (10 if head[5] & 0x10 else 0)

            pos = 0
            id_len = 4 if ver >= 3 else 3
            hdr_len = 10 if ver >= 3 else 6
            while pos + hdr_len <= len(body):
                fid = body[pos:pos + id_len]
                if not fid or fid[0] == 0:
                    break
                try:
                    fid_s = fid.decode("latin-1")
                except Exception:
                    break
                if ver >= 4:
                    fsz = ((body[pos + 4] & 0x7F) << 21) | ((body[pos + 5] & 0x7F) << 14) | \
                          ((body[pos + 6] & 0x7F) << 7) | (body[pos + 7] & 0x7F)
                elif ver >= 3:
                    fsz = struct.unpack(">I", body[pos + 4:pos + 8])[0]
                else:
                    fsz = (body[pos + 3] << 16) | (body[pos + 4] << 8) | body[pos + 5]
                data = body[pos + hdr_len: pos + hdr_len + fsz]
                if len(data) == 0:
                    break
                enc = data[0]
                if fid_s == "APIC" and info["pic_off"] is None:
                    try:
                        p = 1
                        mime_end = data.index(b"\x00", p)
                        p = mime_end + 1
                        p += 1  # picture type
                        desc_end = p
                        # 描述字段（按编码长度不同）
                        if enc in (1, 2):
                            while desc_end + 1 < len(data) and data[desc_end:desc_end + 2] != b"\x00\x00":
                                desc_end += 2
                            p = desc_end + 2
                        else:
                            while desc_end < len(data) and data[desc_end] != 0:
                                desc_end += 1
                            p = desc_end + 1
                        img = data[p:]
                        if img[:3] == b"\xff\xd8\xff" or img[:8] == b"\x89PNG\r\n\x1a\n":
                            info["pic_off"] = body_start + pos + hdr_len + p
                            info["pic_len"] = len(img)
                    except Exception:
                        pass
                elif fid_s in ("TIT2", "TT2"):
                    info["title"] = _decode_text(data[1:], enc)
                elif fid_s in ("TPE1", "TP1"):
                    info["artist"] = _decode_text(data[1:], enc)
                elif fid_s in ("TALB", "TAL"):
                    info["album"] = _decode_text(data[1:], enc)
                elif fid_s in ("TDRC", "TYER", "TYE"):
                    info["year"] = _decode_text(data[1:], enc)[:4]
                pos += hdr_len + fsz
            return info, total
    except Exception:
        return info, 0


def mp3_duration(path, start_offset):
    """扫描 MPEG 帧估算时长（对 CBR/VBR 都准）"""
    try:
        size = os.path.getsize(path)
        frames = 0
        samples_total = 0
        rate_ref = 44100
        with open(path, "rb") as f:
            f.seek(start_offset)
            buf = f.read(2048)
            if not buf:
                return 0, 0
            # 找第一帧
            i = 0
            while i + 4 <= len(buf):
                if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
                    break
                i += 1
            pos = start_offset + i
            kbps_ref = 128
            while pos + 4 <= size:
                f.seek(pos)
                h = f.read(4)
                if len(h) < 4:
                    break
                if h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
                    pos += 1
                    continue
                ver_bits = (h[1] >> 3) & 0x03
                layer_bits = (h[1] >> 1) & 0x03
                br_idx = (h[2] >> 4) & 0x0F
                sr_idx = (h[2] >> 2) & 0x03
                pad = (h[2] >> 1) & 0x01
                if layer_bits != 1:      # 非 Layer III
                    pos += 1
                    continue
                if ver_bits == 1 or br_idx in (0, 15) or sr_idx == 3:
                    pos += 1
                    continue
                if ver_bits == 3:
                    br = MPEG1_L3_BITRATE[br_idx] * 1000
                    sr = SAMPLE_RATES[3][sr_idx]
                    spf = 1152
                    flen = int(144 * br / sr) + pad
                else:
                    br = MPEG2_L3_BITRATE[br_idx] * 1000
                    sr = SAMPLE_RATES[ver_bits][sr_idx]
                    spf = 576
                    flen = int(72 * br / sr) + pad
                if flen <= 4:
                    pos += 1
                    continue
                if frames == 0:
                    rate_ref = sr
                    kbps_ref = br // 1000
                frames += 1
                samples_total += spf
                pos += flen
        if rate_ref <= 0 or frames == 0:
            return 0, 0
        return round(samples_total / rate_ref), kbps_ref
    except Exception:
        return 0, 0


def scan_library():
    """扫描音乐目录，返回曲目列表"""
    items = []
    if not os.path.isdir(MUSIC_DIR):
        return items
    exts = (".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".wma", ".ape")
    names = []
    for dirpath, _dirs, files in os.walk(MUSIC_DIR):
        for fn in files:
            if fn.lower().endswith(exts):
                names.append(os.path.join(dirpath, fn))
    names.sort(key=lambda p: os.path.basename(p).lower())

    for full in names:
        base = os.path.basename(full)
        stem = os.path.splitext(base)[0]
        uid = hashlib.sha1(full.encode("utf-8", "ignore")).hexdigest()[:14]
        title, artist = stem, ""
        if " - " in stem:
            a, b = stem.split(" - ", 1)
            artist, title = a.strip(), b.strip()
        elif "-" in stem and not stem.startswith("-"):
            a, b = stem.split("-", 1)
            if a.strip() and b.strip():
                artist, title = a.strip(), b.strip()

        duration, kbps = 0, 0
        pic = False
        if base.lower().endswith(".mp3"):
            info, off = read_id3(full)
            if info["title"]:
                title = info["title"]
            if info["artist"]:
                artist = info["artist"]
            pic = info["pic_off"] is not None
            duration, kbps = mp3_duration(full, off)
        items.append({
            "id": uid,
            "path": full,
            "file": base,
            "title": title,
            "artist": artist,
            "album": "",
            "duration": duration,
            "kbps": kbps,
            "size": os.path.getsize(full),
            "hasCover": pic,
        })
        print(f"  · {title}" + (f" — {artist}" if artist else "") +
              (f"   [{duration // 60}:{duration % 60:02d} {kbps}k]" if duration else ""))
    return items


# ---------------------------------------------------------------- 全局状态
LIB = []
LIB_MAP = {}
LIB_LOCK = threading.Lock()
URL_CACHE = {}
URL_LOCK = threading.Lock()


def refresh_library(verbose=True):
    global LIB
    t0 = time.time()
    if verbose:
        print(f"[扫描] {MUSIC_DIR}")
    items = scan_library()
    apply_meta(items, load_meta())
    with LIB_LOCK:
        LIB = items
        LIB_MAP.clear()
        for it in items:
            LIB_MAP[it["id"]] = it
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump([{k: v for k, v in it.items() if k != "path"} for it in items],
                      f, ensure_ascii=False)
    except Exception:
        pass
    if verbose:
        print(f"[扫描] 完成，共 {len(items)} 首，耗时 {time.time() - t0:.1f}s")
    return items


DUR_CACHE = {}
DUR_LOCK = threading.Lock()


COVER_LOCKS = {}
COVER_GUARD = threading.Lock()


def _cover_lock(key):
    with COVER_GUARD:
        if key not in COVER_LOCKS:
            COVER_LOCKS[key] = threading.Lock()
        return COVER_LOCKS[key]


def _cover_cleanup():
    """超过上限就按修改时间删掉最旧的一半"""
    try:
        if not os.path.isdir(COVER_DIR):
            return
        files = []
        total = 0
        for fn in os.listdir(COVER_DIR):
            fp = os.path.join(COVER_DIR, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, fp))
            total += st.st_size
        limit = COVER_MAX_MB * 1024 * 1024
        if total <= limit:
            return
        files.sort()
        for _mt, sz, fp in files[: max(1, len(files) // 2)]:
            try:
                os.remove(fp)
            except OSError:
                pass
            total -= sz
            if total <= limit * 0.6:
                break
        print("[封面] 已清理旧缓存，当前 %.1f MB" % (total / 1048576.0))
    except Exception as e:
        print("[封面] 清理失败：%s" % e)


def fetch_cover(pic_id, size="80"):
    """返回 (bytes, mime)；失败 (None, None)。同 id 并发会去重"""
    pic_id = re.sub(r"[^0-9A-Za-z_-]", "", str(pic_id))[:64]
    if not pic_id:
        return None, None
    if str(size) not in ("80", "200", "300", "500"):
        size = "80"
    fn = os.path.join(COVER_DIR, "%s_%s.jpg" % (pic_id, size))
    if os.path.exists(fn):
        try:
            with open(fn, "rb") as f:
                return f.read(), "image/jpeg"
        except OSError:
            pass
    lock = _cover_lock("%s_%s" % (pic_id, size))
    with lock:
        if os.path.exists(fn):
            try:
                with open(fn, "rb") as f:
                    return f.read(), "image/jpeg"
            except OSError:
                pass
        try:
            d = api_get({"types": "pic", "source": "netease",
                         "id": pic_id, "size": str(size)}, timeout=12)
            url = (d or {}).get("url") or ""
            if not url:
                return None, None
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://music.163.com/",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read(1 << 20)
            if not data or len(data) < 100:
                return None, None
            try:
                os.makedirs(COVER_DIR, exist_ok=True)
                with open(fn, "wb") as f:
                    f.write(data)
            except OSError:
                pass
            return data, "image/jpeg"
        except Exception:
            return None, None


def probe_duration(sid, src="netease"):
    """用文件大小与码率反推时长；结果进内存缓存"""
    key = "%s:%s" % (src, sid)
    with DUR_LOCK:
        if key in DUR_CACHE:
            return DUR_CACHE[key]
    dur = 0
    for br in (999, 320):
        try:
            d = api_get({"types": "url", "source": src, "id": sid, "br": br}, timeout=12)
        except Exception:
            continue
        size = (d or {}).get("size") or 0
        real = (d or {}).get("br") or br
        if size and real:
            dur = int(round(size * 8 / (real * 1000)))
            break
    with DUR_LOCK:
        DUR_CACHE[key] = dur
    return dur


def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, */*",
        "Referer": "https://music.gdstudio.xyz/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def api_get(params, timeout=15):
    url = API_BASE + "?" + urllib.parse.urlencode(params)
    return http_json(url, timeout)


# ---------------------------------------------------------------- 元数据补签
def norm_title(s):
    s = (s or "").lower()
    s = re.sub(r"[\(（\[【].*?[\)）\]】]", "", s)
    s = re.sub(r"[\s\-_.,·'\"!?！？、，。~]", "", s)
    return s


def load_meta():
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_meta(m):
    try:
        with open(META_FILE, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ---------------------------------------------------------------- 收藏（我的喜欢·云端部分）
def load_favs():
    try:
        with open(FAV_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, list) else []
    except Exception:
        return []


def save_favs(items):
    try:
        with open(FAV_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        try:
            with open(os.path.join(HERE, "favdebug.log"), "a", encoding="utf-8") as d:
                d.write(f"save ok n={len(items)} path={FAV_FILE}\n")
        except Exception:
            pass
    except Exception as e:
        try:
            with open(os.path.join(HERE, "favdebug.log"), "a", encoding="utf-8") as d:
                d.write(f"save FAIL {e!r} path={FAV_FILE}\n")
        except Exception:
            pass


def fav_key(song):
    return f"{song.get('source', 'netease')}:{song.get('id', '')}"


def fav_ids():
    return {fav_key(s) for s in load_favs()}


def apply_meta(items, meta):
    for it in items:
        m = meta.get(it["id"])
        if not m:
            continue
        if not it.get("artist") and m.get("artist"):
            it["artist"] = m["artist"]
        if not it.get("album") and m.get("album"):
            it["album"] = m["album"]
        if m.get("pic_id"):
            it["pic_id"] = m["pic_id"]
    return items


def patch_meta_bg():
    """后台按曲名联网补全歌手/专辑/封面，仅高置信度匹配才采用"""
    global META_BUSY
    with META_LOCK:
        if META_BUSY:
            return
        META_BUSY = True

    def worker():
        global META_BUSY
        try:
            meta = load_meta()
            with LIB_LOCK:
                todo = [dict(it) for it in LIB
                        if (not it.get("artist") or not it.get("pic_id"))
                        and it["id"] not in meta]
            if not todo:
                return
            print(f"[补签] 待补 {len(todo)} 首")
            done = 0
            lock = threading.Lock()

            def one(it):
                nonlocal done
                nt = norm_title(it["title"])
                rec = {"artist": "", "album": "", "pic_id": ""}
                try:
                    if len(nt) >= 2:
                        res = api_get({"types": "search", "source": "netease",
                                       "name": it["title"], "count": 6}, timeout=10)
                        best, score = None, 0
                        for s in (res or []):
                            cn = norm_title(s.get("name"))
                            if not cn:
                                continue
                            sc = 0
                            if cn == nt:
                                sc = 100
                            elif nt in cn or cn in nt:
                                sc = 70 - abs(len(cn) - len(nt))
                            if sc > score:
                                score, best = sc, s
                        if best and score >= 66:
                            rec["artist"] = " / ".join(best.get("artist") or [])
                            rec["album"] = best.get("album") or ""
                            rec["pic_id"] = best.get("pic_id") or ""
                except Exception:
                    pass
                if rec["artist"] or rec["pic_id"]:
                    with lock:
                        meta[it["id"]] = rec
                with lock:
                    done += 1
                    if done % 8 == 0:
                        save_meta(meta)

            threads = []
            queue = list(todo)
            qlock = threading.Lock()

            def drain():
                while True:
                    with qlock:
                        if not queue:
                            return
                        it = queue.pop()
                    one(it)

            for _ in range(4):
                t = threading.Thread(target=drain, daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join()
            save_meta(meta)
            # 应用到内存曲库
            with LIB_LOCK:
                apply_meta(LIB, meta)
            print(f"[补签] 完成 {len(meta)} 条")
        except Exception as e:
            print(f"[补签] 出错：{e}")
        finally:
            META_BUSY = False

    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------- 请求处理
class Handler(BaseHTTPRequestHandler):
    server_version = "YunShuiJian/1.0"
    protocol_version = "HTTP/1.1"

    def setup(self):
        """每个连接设写超时：客户端切歌/关闭后不再永久占住线程"""
        super().setup()
        try:
            self.connection.settimeout(45)
        except Exception:
            pass

    def log_message(self, fmt, *args):
        if "/api/stream" in (self.path or "") or "/api/proxy" in (self.path or ""):
            return
        sys.stderr.write("  ~ %s\n" % (fmt % args))

    # ---------- 工具 ----------
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def send_error_json(self, msg, code=400):
        self.send_json({"ok": False, "error": msg}, code)

    def parse_range(self, size):
        rng = self.headers.get("Range")
        if not rng:
            return None
        m = rng.strip().replace("bytes=", "").split(",")[0]
        try:
            if m.startswith("-"):
                n = int(m[1:])
                return max(0, size - n), size - 1
            parts = m.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return None
            return start, end
        except Exception:
            return None

    def serve_local_file(self, path, ctype=None, is_head=False):
        try:
            size = os.path.getsize(path)
        except OSError:
            self.send_error_json("文件不存在", 404)
            return
        if not ctype:
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        rng = self.parse_range(size)
        if rng:
            start, end = rng
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            start, end, length = 0, size - 1, size
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        # 页面与脚本一律不走强缓存，避免改动后看到旧界面；音频可缓存
        _ext = os.path.splitext(path)[1].lower()
        if _ext in (".html", ".css", ".js", ".svg", ".ico", ".json"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        else:
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if is_head:
            return
        try:
            remaining = length
            with open(path, "rb") as f:
                f.seek(start)
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout, TimeoutError):
            pass

    # ---------- 路由 ----------
    def do_HEAD(self):
        self.do_GET(head_only=True)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        global MUSIC_DIR
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        def read_body():
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(min(n, 200000)).decode("utf-8", "replace"))

        if route == "/api/config":
            try:
                body = read_body()
                target = str(body.get("musicDir") or "").strip().strip('"').strip("'")
                if not target:
                    return self.send_error_json("请填写音乐目录")
                target = os.path.abspath(target)
                if not os.path.isdir(target):
                    return self.send_error_json("目录不存在：%s" % target)
                MUSIC_DIR = target
                save_config({"musicDir": target})
                items = refresh_library()
                data = [{k: v for k, v in it.items() if k != "path"} for it in items]
                favs = load_favs()
                for f in favs:
                    data.append({
                        "id": str(f.get("id", "")), "kind": "cloud",
                        "source": f.get("source", "netease"),
                        "title": f.get("title", ""), "artist": f.get("artist", ""),
                        "album": f.get("album", ""), "pic_id": f.get("pic_id", ""),
                        "duration": 0, "liked": True,
                    })
                print("[配置] 音乐目录改为 %s（%d 首）" % (target, len(data)))
                return self.send_json({"ok": True, "musicDir": target,
                                       "count": len(data), "songs": data,
                                       "favIds": sorted(fav_ids())})
            except Exception as e:
                return self.send_error_json("设置失败：%s" % e)

        if route == "/api/fav/toggle":
            try:
                song = read_body()
                sid = str(song.get("id", ""))
                src = song.get("source", "netease")
                if not sid:
                    return self.send_error_json("缺少 id")
                favs = load_favs()
                key = f"{src}:{sid}"
                favs = [f for f in favs if fav_key(f) != key]
                liked = bool(song.get("liked"))
                if liked:
                    favs.insert(0, {
                        "id": sid,
                        "source": src,
                        "title": song.get("title", ""),
                        "artist": song.get("artist", ""),
                        "album": song.get("album", ""),
                        "pic_id": song.get("pic_id", ""),
                        "likedAt": int(time.time()),
                    })
                save_favs(favs)
                return self.send_json({"ok": True, "liked": liked,
                                       "favIds": sorted(fav_ids())})
            except Exception as e:
                return self.send_error_json(f"收藏失败：{e}")

        if route == "/api/log":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(min(n, 200000)).decode("utf-8", "replace")
                print("[自检] " + body.replace("\n", "\n[自检] "))
                try:
                    with open(os.path.join(HERE, "selftest.log"), "a", encoding="utf-8") as lf:
                        lf.write(body + "\n---\n")
                except Exception:
                    pass
            except Exception as e:
                print(f"[自检] 读取失败：{e}")
            return self.send_json({"ok": True})

        return self.send_error_json("接口不存在", 404)

    def do_GET(self, head_only=False):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        def q1(k, d=""):
            return (qs.get(k) or [d])[0]

        try:
            if route == "/" or route == "/index.html":
                return self.serve_local_file(os.path.join(STATIC, "index.html"),
                                             "text/html; charset=utf-8", head_only)

            if route.startswith("/static/"):
                rel = route[len("/static/"):]
                safe = os.path.normpath(rel).replace("\\", "/")
                if safe.startswith("..") or ":" in safe:
                    return self.send_error_json("非法路径", 403)
                return self.serve_local_file(os.path.join(STATIC, safe), None, head_only)

            if route == "/favicon.ico":
                fp = os.path.join(STATIC, "icon.ico")
                if os.path.exists(fp):
                    return self.serve_local_file(fp, "image/x-icon", head_only)
                return self.send_error_json("no icon", 404)

            if route == "/api/local":
                with LIB_LOCK:
                    data = [{k: v for k, v in it.items() if k != "path"} for it in LIB]
                favs = load_favs()
                for f in favs:
                    data.append({
                        "id": str(f.get("id", "")),
                        "kind": "cloud",
                        "source": f.get("source", "netease"),
                        "title": f.get("title", ""),
                        "artist": f.get("artist", ""),
                        "album": f.get("album", ""),
                        "pic_id": f.get("pic_id", ""),
                        "duration": 0,
                        "liked": True,
                    })
                return self.send_json({"ok": True, "dir": MUSIC_DIR,
                                       "metaBusy": META_BUSY, "songs": data,
                                       "favIds": sorted(fav_ids())})

            if route == "/api/refresh":
                items = refresh_library()
                data = [{k: v for k, v in it.items() if k != "path"} for it in items]
                favs = load_favs()
                for f in favs:
                    data.append({
                        "id": str(f.get("id", "")),
                        "kind": "cloud",
                        "source": f.get("source", "netease"),
                        "title": f.get("title", ""),
                        "artist": f.get("artist", ""),
                        "album": f.get("album", ""),
                        "pic_id": f.get("pic_id", ""),
                        "duration": 0,
                        "liked": True,
                    })
                return self.send_json({"ok": True, "count": len(data), "songs": data,
                                       "favIds": sorted(fav_ids())})

            if route.startswith("/api/stream/local/"):
                sid = route.rsplit("/", 1)[-1]
                it = LIB_MAP.get(sid)
                if not it:
                    return self.send_error_json("曲目不存在", 404)
                return self.serve_local_file(it["path"], None, head_only)

            if route.startswith("/api/cover/local/"):
                sid = route.rsplit("/", 1)[-1]
                it = LIB_MAP.get(sid)
                if not it or not it.get("hasCover"):
                    return self.send_error_json("无内嵌封面", 404)
                info, _ = read_id3(it["path"])
                if info["pic_off"] is None:
                    return self.send_error_json("无内嵌封面", 404)
                with open(it["path"], "rb") as f:
                    f.seek(info["pic_off"])
                    img = f.read(info["pic_len"])
                ctype = "image/png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(img)))
                self.send_header("Cache-Control", "public, max-age=604800")
                self.end_headers()
                if not head_only:
                    self.wfile.write(img)
                return

            # ---------- 联网搜索 ----------
            if route == "/api/search":
                kw = q1("q").strip()
                limit = int(q1("limit", "30") or 30)
                source = q1("source", "netease")
                if not kw:
                    return self.send_error_json("缺少搜索词")
                try:
                    raw = api_get({"types": "search", "source": source,
                                   "name": kw, "count": max(1, min(limit, 50)),
                                   "pages": 1})
                except Exception as e:
                    return self.send_error_json(f"搜索失败：{e}", 502)
                if not isinstance(raw, list):
                    return self.send_error_json("音源返回异常", 502)
                out = []
                for s in raw:
                    if not s.get("id"):
                        continue
                    out.append({
                        "id": str(s.get("id")),
                        "source": s.get("source") or source,
                        "title": s.get("name") or s.get("title") or "",
                        "artist": " / ".join(s.get("artist") or []) or "未知",
                        "album": s.get("album") or "",
                        "pic_id": s.get("pic_id") or "",
                    })
                return self.send_json({"ok": True, "keyword": kw, "songs": out})

            if route == "/api/url":
                sid = q1("id")
                source = q1("source", "netease")
                if not sid:
                    return self.send_error_json("缺少 id")
                key = f"{source}:{sid}"
                now = time.time()
                with URL_LOCK:
                    c = URL_CACHE.get(key)
                    if c and c[0] > now:
                        return self.send_json({"ok": True, "url": c[1], "br": c[2],
                                               "quality": c[3], "cached": True})
                last_err = ""
                for br, label in ((999, "无损"), (320, "320K"), (192, "192K")):
                    try:
                        d = api_get({"types": "url", "source": source,
                                     "id": sid, "br": br}, timeout=20)
                    except Exception as e:
                        last_err = str(e)
                        continue
                    u = (d or {}).get("url") or ""
                    if u:
                        real = (d or {}).get("br") or br
                        qlab = label if br != 999 else ("FLAC" if real > 320 else "320K")
                        with URL_LOCK:
                            URL_CACHE[key] = (now + 600, u, real, qlab)
                        return self.send_json({"ok": True, "url": u, "br": real,
                                               "quality": qlab})
                return self.send_error_json(f"该曲目暂无可用音源{('：' + last_err) if last_err else ''}", 404)

            if route == "/api/config":
                return self.send_json({
                    "ok": True,
                    "musicDir": MUSIC_DIR,
                    "exists": os.path.isdir(MUSIC_DIR),
                    "dataDir": DATA_DIR,
                })

            if route == "/api/durations":
                ids = [x for x in (q1("ids") or "").split(",") if x][:40]
                src = q1("source", "netease")
                res = {}
                if ids:
                    try:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                            for sid, dur in ex.map(lambda x: (x, probe_duration(x, src)), ids):
                                res[sid] = dur
                    except Exception:
                        pass
                return self.send_json({"ok": True, "map": res})

            if route == "/api/proxy":
                raw_url = q1("u")
                if not raw_url:
                    return self.send_error_json("缺少地址")
                return self.proxy_stream(raw_url, head_only)

            if route == "/api/lyric":
                sid = q1("id")
                source = q1("source", "netease")
                try:
                    d = api_get({"types": "lyric", "source": source, "id": sid}, timeout=15)
                except Exception as e:
                    return self.send_error_json(f"歌词获取失败：{e}", 502)
                return self.send_json({"ok": True,
                                       "lyric": (d or {}).get("lyric", "") or "",
                                       "tlyric": (d or {}).get("tlyric", "") or ""})

            if route == "/api/cover":
                pic = q1("id")
                size = q1("size", "200")
                if not pic:
                    return self.send_error_json("缺少封面 id")
                data, mime = fetch_cover(pic, size)
                if not data:
                    return self.send_error_json("无封面", 404)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=604800")
                self.end_headers()
                if not head_only:
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError, socket.timeout):
                        pass
                return

            if route == "/api/covers/warm":
                ids = [x for x in (q1("ids") or "").split(",") if x][:80]
                size = q1("size", "80")
                if ids:
                    def _warm():
                        try:
                            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                                list(ex.map(lambda x: fetch_cover(x, size), ids))
                            _cover_cleanup()
                            print("[封面] 预热完成 %d 张" % len(ids))
                        except Exception:
                            pass
                    threading.Thread(target=_warm, daemon=True).start()
                return self.send_json({"ok": True, "count": len(ids)})
                return

            if route == "/api/ping":
                return self.send_json({"ok": True, "count": len(LIB), "dir": MUSIC_DIR})

            if route == "/api/quit":
                self.send_json({"ok": True})
                print("[退出] 收到收卷指令，服务关闭")
                threading.Timer(0.5, lambda: os._exit(0)).start()
                return
            return self.send_error_json("接口不存在", 404)

        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout, TimeoutError):
            pass
        except Exception as e:
            try:
                self.send_error_json(f"服务端错误：{e}", 500)
            except Exception:
                pass

    def proxy_stream(self, raw_url, head_only=False):
        try:
            target = urllib.parse.unquote(raw_url)
        except Exception:
            target = raw_url
        if not target.startswith(("http://", "https://")):
            return self.send_error_json("地址非法")
        headers = {
            "User-Agent": UA,
            "Referer": "https://music.163.com/",
            "Accept": "*/*",
        }
        rng = self.headers.get("Range")
        if rng:
            headers["Range"] = rng
        req = urllib.request.Request(target, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=25)
        except urllib.error.HTTPError as e:
            if e.code in (416,):
                self.send_response(416)
                self.send_header("Content-Range", e.headers.get("Content-Range", "*"))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self.send_error_json(f"音源连接失败 {e.code}", 502)
        except Exception as e:
            return self.send_error_json(f"音源连接失败：{e}", 502)

        try:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            length = resp.headers.get("Content-Length")
            status = resp.status
            if head_only:
                if not ctype or "octet-stream" in ctype:
                    ctype = "audio/mpeg"
                self.send_response(206 if status == 206 else 200)
                self.send_header("Content-Type", ctype)
                if length:
                    self.send_header("Content-Length", length)
                else:
                    self.close_connection = True
                cr = resp.headers.get("Content-Range")
                if cr:
                    self.send_header("Content-Range", cr)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                resp.close()
                return

            # 先嗅探文件头，纠正 MIME（上游常把 flac 标成 audio/mpeg）
            head = resp.read(1024)
            if head[:4] == b"fLaC":
                real_type = "audio/flac"
            elif head[:4] == b"OggS":
                real_type = "audio/ogg"
            elif head[:4] == b"RIFF":
                real_type = "audio/wav"
            elif head[:4] == b"ftyp" or head[4:8] == b"ftyp":
                real_type = "audio/mp4"
            elif head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
                real_type = "audio/mpeg"
            else:
                real_type = ctype if ctype and "octet-stream" not in ctype else "audio/mpeg"

            self.send_response(206 if status == 206 else 200)
            self.send_header("Content-Type", real_type)
            if length:
                self.send_header("Content-Length", length)
            else:
                # 上游用 chunked 时没有长度，keep-alive 下浏览器会一直空等
                self.send_header("Connection", "close")
                self.close_connection = True
            cr = resp.headers.get("Content-Range")
            if cr:
                self.send_header("Content-Range", cr)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(head)
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout, TimeoutError):
            pass
        finally:
            try:
                resp.close()
            except Exception:
                pass


# ---------------------------------------------------------------- 启动
def already_running(port):
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def open_browser(url):
    if os.environ.get("YSJ_NO_BROWSER") == "1":
        return

    def _go():
        time.sleep(0.8)
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def main():
    print("=" * 56)
    print("  云水间 · 听音   本地音乐播放器")
    print("=" * 56)
    if not os.path.isdir(MUSIC_DIR):
        print(f"[警告] 音乐目录不存在：{MUSIC_DIR}")
    refresh_library(verbose=True)
    patch_meta_bg()

    port = None
    for p in PORT_CANDIDATES:
        if already_running(p):
            print(f"[提示] 端口 {p} 已有服务运行，直接打开浏览器")
            open_browser(f"http://127.0.0.1:{p}/")
            time.sleep(1.2)
            return
        port = p
        break
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"[错误] 无法监听端口 {port}：{e}")
        input("按回车退出...")
        return
    httpd.daemon_threads = True
    print(f"[服务] http://127.0.0.1:{port}/")
    print("[服务] 关闭此窗口即停止播放器")
    open_browser(f"http://127.0.0.1:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[退出] 已停止")


if __name__ == "__main__":
    main()
