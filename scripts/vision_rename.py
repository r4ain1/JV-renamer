# -*- coding: utf-8 -*-
"""
无语义文件视觉识别重命名器 (vision_rename.py)
================================================================
针对 Telegram 下载/网盘抓取的 `protected_content_*` / `media_group_*` /
`8月X日` / `[杂项]` 类无语义文件，按 ffmpeg 均匀抽帧或固定秒数间隔抽帧 +
多模态视觉 LLM 评估场景、特色、剧情及出镜明星/女优/厂牌，生成规范文件名。

用法:
    python vision_rename.py --dir "C:/Users/Nero/Downloads"
    python vision_rename.py --dir "C:/Users/Nero/Downloads" --apply
    python vision_rename.py --dir "C:/..." --frames 8 --model group/flash
    python vision_rename.py --dir "C:/..." --interval 15 --max-frames 12 --include-misc --apply

依赖:
    - ffmpeg / ffprobe 在 PATH 或 /app/bin 中
    - Pillow (可选；未安装时自动回退到纯 ffmpeg tile 滤镜拼图)
    - OpenAI 兼容视觉接口 (默认 http://127.0.0.1:3425/v1)
"""

import argparse
import base64
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.wmv', '.m4v', '.flv', '.rmvb', '.mov', '.ts'}
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def find_binary(name, explicit=None):
    if explicit and os.path.exists(explicit):
        return explicit
    found = shutil.which(name)
    if found:
        return found
    for cand in (f"/app/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}"):
        if os.path.exists(cand):
            return cand
    return name


def is_local_or_private(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        if host in ("localhost", "127.0.0.1", "::1", "tg-magpie", "9router"):
            return True
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or str(ip).startswith("100.")
    except Exception:
        return False


def format_ts(sec):
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

# ---------- 1. 抽帧 ----------

def probe_duration(path, ffprobe_bin="ffprobe"):
    try:
        out = subprocess.check_output([
            ffprobe_bin, '-v', 'quiet', '-show_entries', 'format=duration',
            '-of','csv=p=0', path
        ], timeout=15).decode().strip()
        return float(out)
    except Exception:
        return None

def compute_timestamps(dur, n_frames=8, interval_sec=0.0, max_frames=12):
    """计算抽帧时间戳列表：支持固定间隔抽帧（自动封顶并覆盖全片）或均匀抽 N 帧"""
    if dur <= 2:
        return []
    lo, hi = dur * 0.05, dur * 0.95
    if hi <= lo:
        lo, hi = 0.5, max(0.5, dur - 0.5)
    if interval_sec and interval_sec > 0:
        span = max(hi - lo, 1.0)
        count = int(span // interval_sec) + 1
        count = max(2, min(count, max_frames))
        if count == 1:
            return [(lo + hi) / 2.0]
        step = (hi - lo) / (count - 1)
        return [lo + i * step for i in range(count)]
    count = max(1, min(n_frames, max_frames))
    if count == 1:
        return [(lo + hi) / 2.0]
    step = (hi - lo) / (count - 1)
    return [lo + i * step for i in range(count)]

def extract_frames(video_path, out_dir, n_frames=8, interval_sec=0.0, max_frames=12, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe"):
    """抽帧到 out_dir，返回 [(帧路径, 秒数), ...]"""
    dur = probe_duration(video_path, ffprobe_bin=ffprobe_bin)
    if not dur or dur < 5:
        return []
    timestamps = compute_timestamps(dur, n_frames=n_frames, interval_sec=interval_sec, max_frames=max_frames)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    safe = re.sub(r'[^\w\-]', '_', stem)[:40]
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for i, t in enumerate(timestamps):
        fp = os.path.join(out_dir, f"{safe}_f{i+1:02d}.jpg")
        subprocess.run([
            ffmpeg_bin, '-y', '-ss', f'{t:.1f}', '-i', video_path,
            '-vframes', '1', '-vf', 'scale=960:-2', '-q:v', '3', fp
        ], capture_output=True, timeout=30)
        if os.path.exists(fp) and os.path.getsize(fp) > 5000:
            frames.append((fp, t))
    return frames

def make_grid(frames, out_path, cols=4, ffmpeg_bin="ffmpeg"):
    """把多帧拼成一张网格图（优先 Pillow 标注序号与时间戳，无 Pillow 时自动回退纯 ffmpeg tile 滤镜）"""
    if not frames:
        return None
    frame_paths = [f[0] if isinstance(f, tuple) else f for f in frames]
    frame_times = [f[1] if isinstance(f, tuple) else None for f in frames]
    cols = max(1, min(cols, len(frame_paths)))
    rows = (len(frame_paths) + cols - 1) // cols
    try:
        from PIL import Image, ImageDraw
        imgs = [Image.open(p).convert('RGB') for p in frame_paths]
        w, h = imgs[0].size
        tw = min(480, max(240, w // 2))
        th = max(135, int(h * (tw / max(w, 1))))
        thumbs = [im.resize((tw, th)) for im in imgs]
        canvas = Image.new('RGB', (tw * cols, th * rows), (20, 20, 20))
        draw = ImageDraw.Draw(canvas)
        for i, im in enumerate(thumbs):
            x, y = (i % cols) * tw, (i // cols) * th
            canvas.paste(im, (x, y))
            label = f"#{i+1}" if frame_times[i] is None else f"#{i+1} {format_ts(frame_times[i])}"
            box_w = 18 + len(label) * 7
            draw.rectangle([x, y, x + box_w, y + 18], fill=(0, 0, 0))
            draw.text((x + 4, y + 2), label, fill=(255, 220, 0))
        canvas.save(out_path, 'JPEG', quality=85)
        return out_path
    except ImportError:
        list_file = out_path + ".concat.txt"
        try:
            with open(list_file, "w", encoding="utf-8") as lf:
                for p in frame_paths:
                    esc = p.replace("\\", "/").replace("'", "'\\''")
                    lf.write(f"file '{esc}'\n")
            vf = f"scale=480:-2,tile={cols}x{rows}:padding=4:color=0x141414"
            res = subprocess.run([
                ffmpeg_bin, '-y', '-f', 'concat', '-safe', '0', '-i', list_file,
                '-frames:v', '1', '-vf', vf, '-q:v', '4', out_path
            ], capture_output=True, timeout=45)
            if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 3000:
                return out_path
        except Exception:
            return None
        finally:
            if os.path.exists(list_file):
                try:
                    os.remove(list_file)
                except Exception:
                    pass
        return None

# ---------- 2. 视觉 LLM ----------

SYS_PROMPT = '''你是成人影片内容识别与文件命名助手。输入为一段视频的多帧时间序拼图（从左到右、从上到下按时间推进 #1..#N），有时还会附带原文件名或 Telegram 消息正文作为辅助线索。
请综合所有帧的画面变化，重点评估【场景、特色亮点、剧情脉络、出镜明星/演员、厂牌水印】，并输出纯 JSON（无 markdown 代码块）：
{
 "category": "AI换脸|JAV|国产传媒|欧美|里番|3D同人|杂项 之一",
 "studio": "厂牌/水印，如 IPPA/FALENO/S1/麻豆/果冻/天美/SA国际/Dorcel/Brazzers/OnlyFans 等，无则null",
 "actress": "出镜明星或知名女优名（如 AI换脸目标明星：迪丽热巴/杨幂/刘亦菲/赵露思/范冰冰/杨颖等，或画面文字明确标示的演员名；不确定则null）",
 "scene": "场景环境（卧室/办公室/教室/浴室/酒店/车内/户外等，中文4-12字）",
 "features": "核心特色亮点（服装如黑丝/OL制服/空姐/旗袍/汉服/护士，或拍摄风格/身材/玩法特色，中文4-14字，无则null）",
 "costume": "服装特征（黑丝/OL制服/空姐制服/新娘白丝等，无则null）",
 "plot": "剧情一句话（中文10-25字，连贯概括多帧场景与情节发展）",
 "tags": ["3-4个精准中文标签，如['人妻','黑丝','办公室','制服']"]
}
硬性规则：
- 优先结合画面水印、字幕文字、服装场景与辅助正文判断分类与剧情；AI换脸视频可识别「换脸目标明星」公众形象标签，普通素人严禁编造真实姓名
- 若画面中出现清晰番号（如 IPZZ-479、MD-0336），请在 studio 或 plot 中准确保留
- 若画面纯黑/全程模糊无法辨认，category 返回 "杂项"
- 若疑似损坏文件（首帧全黑/绿屏/大面积花屏马赛克）返回 {"category":"损坏"}
'''

def call_vision(img_path, api_base, api_key, model, timeout=120, extra_context=""):
    with open(img_path,'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    prompt_text = "请仔细观察拼图中的多帧画面，评估场景、特色、剧情及出镜明星/厂牌，输出JSON。"
    if extra_context:
        prompt_text += f"\n辅助线索（消息正文/原名）：{extra_context[:500]}"
    models_to_try = [model] if model == 'group/smart' else [model, 'group/smart']
    opener = NO_PROXY_OPENER if is_local_or_private(api_base) else urllib.request.build_opener()
    for m in models_to_try:
        body = json.dumps({
            "model": m,
            "messages":[
                {"role":"system","content":SYS_PROMPT},
                {"role":"user","content":[
                    {"type":"text","text":prompt_text},
                    {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}}
                ]}
            ],
            "temperature":0.2,"max_tokens":1500
        }).encode()
        try:
            req = urllib.request.Request(
                f"{api_base.rstrip('/')}/chat/completions", data=body,
                headers={'Content-Type':'application/json','Authorization':f'Bearer {api_key}'})
            res = opener.open(req, timeout=timeout).read().decode()
            msg = json.loads(res)['choices'][0].get('message', {})
            content = (msg.get('content') or '').strip()
            content = re.sub(r'^```(json)?|```$','',content).strip()
            matched = re.search(r'\{.*\}', content, re.S)
            if matched:
                return json.loads(matched.group(0))
        except Exception:
            continue
    return None

# ---------- 3. 命名生成 ----------

def build_name(meta, ext):
    """根据识别结果生成规范文件名"""
    if not meta:
        return None
    cat = meta.get('category','杂项')
    if cat == '损坏':
        return f"[损坏]{ext}"
    studio = meta.get('studio') or ''
    actress = meta.get('actress') or ''
    scene = meta.get('scene') or ''
    features = meta.get('features') or ''
    costume = meta.get('costume') or ''
    plot = meta.get('plot') or ''
    tags = meta.get('tags') or []
    highlight = features or costume

    if cat == 'AI换脸':
        parts = ['[AI换脸]', actress or '未知', plot or scene]
    elif cat == '国产传媒':
        parts = [f'[{studio or "国产"}]', plot or scene]
        if actress: parts.append(f'[{actress}]')
    elif cat == 'JAV':
        parts = ['[JAV]']
        if studio: parts.append(f'[{studio}]')
        if highlight and highlight not in (plot or ''): parts.append(highlight)
        parts.append(plot or scene)
        if actress: parts.append(f'[{actress}]')
    elif cat == '欧美':
        parts = [f'[欧美·{studio}]' if studio else '[欧美]', plot or scene]
        if actress: parts.append(f'[{actress}]')
    elif cat in ('里番','3D同人'):
        parts = [f'[{cat}]', plot or scene or '作品']
        if studio: parts.append(f'[{studio}]')
    else:
        desc = plot or ' '.join(x for x in (scene, highlight) if x) or '待分类'
        parts = ['[杂项]', desc]
        if actress: parts.append(f'[{actress}]')

    # 附标签
    if tags:
        t = '·'.join(t for t in tags[:4] if t)
        if t: parts.append(f'[{t}]')

    name = ' '.join(p for p in parts if p).strip()
    name = re.sub(r'[/\\:*?"<>|]',' ',name)
    name = re.sub(r'\s+',' ',name).strip()
    return f"{name}{ext}"

def needs_vision(fname, include_misc=False):
    """判断是否无语义命名（需要视觉识别）"""
    stem = os.path.splitext(fname)[0]
    if include_misc and re.match(r'^\[杂项\]', stem):
        if re.search(r'\[[^\]]+·[^\]]+\](\s*\(\d+\))?$', stem):
            return False
        return True
    if re.match(r'^\[', stem): return False  # 已规范
    if re.match(r'^[A-Za-z]{2,6}-\d{2,5}', stem): return False  # 标准番号
    if re.match(r'^\d{1,2}月\d{1,2}日', stem):
        return True
    if re.search(r'[\u4e00-\u9fff]', stem): return False  # 含中文有语义
    # 纯数字
    if re.match(r'^\d+$', stem): return True
    # 包含 watermarked
    if 'watermarked' in stem.lower(): return True
    # protected_content_*/media_group_*/日期/数字hash/纯英文随机
    if re.match(r'^(protected_content|media_group|video|VID_|IMG_|MOV_|download|\d+月\d+日|\d{4}-\d{2}-\d{2}|[a-f0-9]{12,})', stem, re.I):
        return True
    # 全英文但无单词意义 > 无语义
    words = re.findall(r'[a-z]{3,}', stem.lower())
    if len(words) <= 1 and len(stem) > 8:
        return True
    return False

# ---------- 4. 主流程 ----------

def process_one(f, target_dir, frames_dir, args, extra_context=""):
    """处理单个文件：抽帧 -> 拼图 -> 识别 -> 返回新文件名"""
    video_path = os.path.join(target_dir, f)
    stem, ext = os.path.splitext(f)
    ffmpeg_bin = find_binary("ffmpeg", getattr(args, "ffmpeg", None))
    ffprobe_bin = find_binary("ffprobe", getattr(args, "ffprobe", None))
    created_files = []
    try:
        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            return f, f"[损坏零字节]_{stem}{ext}", {"category": "损坏"}
        dur = probe_duration(video_path, ffprobe_bin=ffprobe_bin)
        if dur is None or dur < 2:
            return f, f"[损坏]_{stem}{ext}", {"category": "损坏"}
        frames = extract_frames(
            video_path, frames_dir,
            n_frames=args.frames,
            interval_sec=args.interval,
            max_frames=args.max_frames,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )
        if not frames:
            return f, f"[损坏]_{stem}{ext}", {"category": "损坏"}
        created_files.extend(p for p, _ in frames)
        grid_path = os.path.join(frames_dir, re.sub(r'[^\w\-]','_',stem)[:40] + '_grid.jpg')
        if not make_grid(frames, grid_path, cols=min(4, len(frames)), ffmpeg_bin=ffmpeg_bin):
            return f, None, 'grid_fail'
        created_files.append(grid_path)
        meta = call_vision(grid_path, args.api_base, args.api_key, args.model, extra_context=extra_context)
        if not meta:
            return f, None, 'vision_fail'
        new_name = build_name(meta, ext)
        return f, new_name, meta
    except Exception as e:
        return f, None, f'error: {e}'
    finally:
        if not getattr(args, "keep_frames", False):
            for p in created_files:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

def main():
    ap = argparse.ArgumentParser(description='无语义视频文件视觉识别重命名')
    ap.add_argument('--dir', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--frames', type=int, default=8, help='均匀抽帧数(默认8)')
    ap.add_argument('--interval', type=float, default=0.0, help='按固定间隔抽帧（秒，如 10 表示每隔10秒截1帧，封顶 --max-frames）')
    ap.add_argument('--max-frames', type=int, default=12, help='单部视频最大抽帧数(默认12)')
    ap.add_argument('--include-misc', action='store_true', help='同时对已归入 [杂项] 且无视觉标签的文件执行抽帧分析')
    ap.add_argument('--context-json', default=None, help='外部上下文 JSON 路径（如 TG 消息正文），默认自动探测 _context.json')
    ap.add_argument('--limit', type=int, default=0, help='本次最多处理文件数（0表示不限制，调试预览用）')
    ap.add_argument('--keep-frames', action='store_true', help='保留 _vision_frames 抽帧截图（默认自动清理）')
    ap.add_argument('--ffmpeg', default=None, help='自定义 ffmpeg 路径')
    ap.add_argument('--ffprobe', default=None, help='自定义 ffprobe 路径')
    ap.add_argument('--api-base', default='http://127.0.0.1:3425/v1')
    ap.add_argument('--api-key', default=os.getenv('MAGPIE_API_KEY','magpie'))
    ap.add_argument('--model', default='group/flash')
    ap.add_argument('--concurrency', type=int, default=3)
    ap.add_argument('--cache', default=None, help='识别结果缓存json路径')
    args = ap.parse_args()

    target = os.path.abspath(args.dir)
    frames_dir = os.path.join(target, '_vision_frames')
    os.makedirs(frames_dir, exist_ok=True)
    cache_path = args.cache or os.path.join(target, '_vision_cache.json')

    context_map = {}
    ctx_path = args.context_json or os.path.join(target, '_context.json')
    if ctx_path and os.path.exists(ctx_path):
        try:
            raw_ctx = json.load(open(ctx_path, encoding='utf-8'))
            if isinstance(raw_ctx, dict):
                context_map = {str(k): str(v) for k, v in raw_ctx.items() if v}
        except Exception:
            pass

    cache = {}
    if os.path.exists(cache_path):
        try: cache = json.load(open(cache_path,encoding='utf-8'))
        except Exception: pass

    files = [f for f in sorted(os.listdir(target))
             if os.path.isfile(os.path.join(target,f))
             and os.path.splitext(f)[1].lower() in VIDEO_EXTS
             and needs_vision(f, include_misc=args.include_misc)]
    if args.limit and args.limit > 0:
        files = files[:args.limit]
    print(f'[*] 待识别文件: {len(files)} 个')

    plan = {}
    pending = [f for f in files if f not in cache]
    if pending:
        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            futs = {ex.submit(process_one, f, target, frames_dir, args, context_map.get(f, "")): f for f in pending}
            for i,fut in enumerate(as_completed(futs),1):
                f,new_name,meta = fut.result()
                if isinstance(meta,dict):
                    cache[f] = {'meta':meta,'new_name':new_name}
                    print(f'  [{i}/{len(pending)}] {f[:40]:<42} -> {new_name[:50] if new_name else "?"}')
                else:
                    print(f'  [{i}/{len(pending)}] {f[:40]:<42} -> SKIP ({meta})')
                if i%5==0:
                    json.dump(cache,open(cache_path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
        json.dump(cache,open(cache_path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    if not args.keep_frames:
        try:
            if os.path.isdir(frames_dir) and not os.listdir(frames_dir):
                os.rmdir(frames_dir)
        except Exception:
            pass

    # 应用
    applied=0
    for f in files:
        rec = cache.get(f)
        if not rec: continue
        new = rec.get('new_name')
        if not new or new==f: continue
        src,dst = os.path.join(target,f), os.path.join(target,new)
        if os.path.exists(src):
            if os.path.exists(dst):
                base, ext = os.path.splitext(new)
                idx = 2
                while os.path.exists(os.path.join(target, f"{base} ({idx}){ext}")):
                    idx += 1
                new = f"{base} ({idx}){ext}"
                dst = os.path.join(target, new)
            try:
                if args.apply:
                    os.rename(src,dst); applied+=1
                else:
                    plan[f]=new
            except PermissionError:
                print(f'  [!] 锁定: {f}')
    if args.apply:
        print(f'\n[✓] 已重命名 {applied} 个')
    else:
        print(f'\n[*] 预览计划 {len(plan)} 条（加 --apply 执行）')
        for old,new in list(plan.items())[:20]:
            print(f'  {old[:50]} -> {new[:60]}')

if __name__=='__main__':
    main()
