"""Local URL/file -> bounded reference shot. No cloud generation or uploads."""
import hashlib
import html
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse, parse_qs

import av
import imageio_ffmpeg
import folder_paths
from comfy_api.latest import ComfyExtension, io, InputImpl, Types


def _run(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf8', errors='replace',
                            creationflags=0x08000000 if os.name == 'nt' else 0, timeout=600)
    if result.returncode:
        raise RuntimeError('视频处理失败：' + result.stderr[-1500:])


def _probe(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        return stream.frames, stream.width, stream.height


def _output_size(width, height, long_edge):
    scale = long_edge / max(width, height)
    return max(32, round(width * scale / 32) * 32), max(32, round(height * scale / 32) * 32)


def _check_cancel():
    from comfy.model_management import throw_exception_if_processing_interrupted
    throw_exception_if_processing_interrupted()


def _normalize_url(text):
    text = html.unescape(text).strip().replace('\u200b', '').replace('\ufeff', '')
    if not text:
        return ''
    links = re.findall(r'''(?:https?://|www\.)[^\s<>"'，。；！？、【】「」“”]+''', text, re.I)
    links = list(dict.fromkeys(s.rstrip('.,;!?)）]}…') for s in links))
    if len(links) != 1:
        raise ValueError('请粘贴一个视频链接或包含一个链接的分享文字；一次只处理一个视频。')
    url = links[0]
    if url.lower().startswith('www.'):
        url = 'https://' + url
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('请使用有效的 http/https 视频链接，不要在链接中填写账号密码。')
    if _is_douyin(url):
        video_id = _douyin_id(url)
        if video_id:
            return f'https://www.douyin.com/video/{video_id}'
    return url


def _is_douyin(url):
    host = (urlparse(url).hostname or '').lower()
    return any(host == domain or host.endswith('.' + domain) for domain in ('douyin.com', 'iesdouyin.com'))


def _douyin_id(url):
    parsed = urlparse(url)
    match = re.search(r'/(?:share/)?video/(\d+)(?:/|$)', parsed.path)
    if match:
        return match[1]
    query = parse_qs(parsed.query)
    for key in ('modal_id', 'vid', 'aweme_id', 'item_id'):
        value = query.get(key, [''])[0]
        if re.fullmatch(r'\d{6,}', value):
            return value
    return None


def _resolve_douyin(url):
    if not _is_douyin(url) or _douyin_id(url):
        return url
    import requests
    with requests.Session() as session:
        session.max_redirects = 5
        with session.get(url, timeout=(15, 25), stream=True) as response:
            response.raise_for_status()
            resolved = _normalize_url(response.url)
    if not _is_douyin(resolved) or not _douyin_id(resolved):
        raise ValueError('这个抖音链接没有指向单个视频。请使用视频的“分享→复制链接”，不要复制作者主页或直播链接。')
    return resolved


def _read_douyin_media(url):
    """Read the public player in a fresh browser; no personal profile or login cookies."""
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    video_id = _douyin_id(url)
    select_video = '''(id) => Array.from(document.querySelectorAll('video')).find(v => {
        try {
            const u = new URL(v.currentSrc);
            return v.videoWidth > 16 && v.duration > 0 &&
                u.hostname.endsWith('.douyinvod.com') && u.searchParams.get('__vid') === id;
        } catch { return false; }
    })?.currentSrc || '' '''
    with sync_playwright() as p:
        channel = 'chrome' if (Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe').is_file() else 'msedge'
        browser = p.chromium.launch(channel=channel, headless=True, chromium_sandbox=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                _check_cancel()
                try:
                    handle = page.wait_for_function(select_video, arg=video_id, timeout=1000)
                    return handle.json_value()
                except BrowserTimeout:
                    pass
            raise RuntimeError('抖音公开播放器未返回这条视频。可能需要登录/验证，或视频已下架；请在浏览器确认可播放，必要时保存视频后填写 local_path。')
        finally:
            browser.close()


def _download_douyin_browser(url, taskdir):
    import requests
    # ComfyUI can call synchronous nodes from an asyncio loop; isolate Playwright.
    with ThreadPoolExecutor(max_workers=1) as executor:
        media_url = executor.submit(_read_douyin_media, url).result()
    target = taskdir / 'source.mp4'
    partial = taskdir / 'source.browser.part'
    try:
        headers = {'Referer': url, 'User-Agent': 'Mozilla/5.0'}
        with requests.get(media_url, headers=headers, stream=True, timeout=(15, 30)) as response:
            response.raise_for_status()
            size = 0
            deadline = time.monotonic() + 300
            with partial.open('wb') as output:
                for chunk in response.iter_content(1024 * 1024):
                    _check_cancel()
                    size += len(chunk)
                    if size > 2 * 1024**3 or time.monotonic() > deadline:
                        raise RuntimeError('参考视频超过 2GB 或下载超过 5 分钟，请使用较短的视频。')
                    output.write(chunk)
        _probe(partial)
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def _download_reference(url, taskdir, ffmpeg):
    import yt_dlp
    opts = {'noplaylist': True, 'playlistend': 1,
        'format': 'bestvideo[height<=2160]+bestaudio/best[height<=2160]/best',
        'outtmpl': str(taskdir / 'source.%(ext)s'), 'merge_output_format': 'mp4',
        'max_filesize': 2*1024**3, 'socket_timeout': 25, 'retries': 2, 'fragment_retries': 2,
        'quiet': True, 'no_warnings': False, 'overwrites': False,
        'ffmpeg_location': ffmpeg, 'progress_hooks': [lambda _: _check_cancel()]}
    node = shutil.which('node') or str(Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'nodejs/node.exe')
    if Path(node).is_file():
        opts['js_runtimes'] = {'node': {'path': node}}
    if urlparse(url).hostname in ('127.0.0.1', 'localhost', '::1'):
        opts['proxy'] = ''
    logging.info('[ProductReference] Downloading reference video')
    try:
        with yt_dlp.YoutubeDL(opts) as downloader:
            info = downloader.extract_info(url, download=False)
            if info.get('_type') in ('playlist', 'multi_video'):
                raise ValueError('请使用单个视频链接，不支持整份播放列表或作者主页。')
            downloader.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as exc:
        _check_cancel()
        if _is_douyin(url) and _douyin_id(url):
            logging.info('[ProductReference] Using the public Douyin browser player')
            try:
                return _download_douyin_browser(url, taskdir)
            except Exception as browser_exc:
                _check_cancel()
                raise RuntimeError('抖音下载未完成：' + str(browser_exc)[-600:]) from browser_exc
        detail = re.sub(r'\x1b\[[0-9;]*m', '', str(exc))[-600:]
        raise RuntimeError('该链接暂时无法下载。支持范围取决于平台是否公开提供视频；登录、地区限制、失效链接可能失败。可保存视频后清空 video_url，填写 local_path。原因：' + detail) from exc
    candidates = [p for p in taskdir.glob('source.*') if p.suffix.lower() in ('.mp4', '.mkv', '.webm', '.mov', '.avi')]
    if not candidates:
        raise RuntimeError('未下载到视频，可能超过大小限制或链接不含视频。请使用单个视频页面、视频直链或本地文件。')
    source = max(candidates, key=lambda p: p.stat().st_mtime)
    _probe(source)
    return source


class ProductReferenceShot(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id='ProductReferenceShot', display_name='① 参考视频链接 / 本地视频 → 截取镜头',
            category='video/product', description='支持单个视频页面、抖音分享短链、整段分享文字和视频直链。只提供生成参考，不登记为生成成片。抖音接口失败时自动读取公开浏览器播放器。登录、验证码、地区限制和加密内容不能保证下载。',
            inputs=[
                io.String.Input('local_path', default='reference-robot-test.mp4', tooltip='输入目录中的文件名，或本地视频的完整路径。链接不为空时优先使用链接。'),
                io.String.Input('video_url', default='', multiline=True, tooltip='可直接粘贴整段分享文字。支持抖音、TikTok等下载器支持的网站及MP4等直链；一次一个视频。链接为空时使用local_path。'),
                io.Float.Input('start_seconds', default=0.0, min=0, max=86400, step=0.1),
                io.Float.Input('duration_seconds', default=5.2, min=0, max=3600, step=0.1, tooltip='0 表示从开始位置读取到视频结束；08 的帧数上限也设为 0 时输出完整时长。'),
                io.Int.Input('reference_long_edge', default=384, min=192, max=2048, step=32),
                io.Int.Input('download_revision', default=0, min=0, max=999999, tooltip='同一个网址内容更换时加 1，重新下载。'),
                io.Video.Input('local_video', optional=True),
                io.Int.Input('生成长边', default=672, min=384, max=1024, step=32, optional=True, tooltip='生成尺寸按参考视频比例自动计算，避免竖屏被挤成宽画幅。越大越慢。'),
                io.Boolean.Input('preserve_source', default=False, optional=True, tooltip='09开启：完整读取时保留原视频分辨率与原文件，避免预先缩小。'),
            ],
            outputs=[io.Video.Output(display_name='参考镜头'), io.Int.Output(display_name='生成帧数'),
                     io.Int.Output(display_name='生成宽度'), io.Int.Output(display_name='生成高度')])

    @classmethod
    def execute(cls, local_path, video_url, start_seconds, duration_seconds, reference_long_edge, download_revision, local_video=None, 生成长边=672, preserve_source=False):
        ffmpeg = shutil.which('ffmpeg') or imageio_ffmpeg.get_ffmpeg_exe()
        if not ffmpeg:
            raise RuntimeError('找不到 FFmpeg，请安装后重启 ComfyUI。')
        url = _normalize_url(video_url)
        cache = Path(folder_paths.get_input_directory()) / 'reference-downloads'
        cache.mkdir(exist_ok=True)
        if url:
            url = _resolve_douyin(url)
            key = hashlib.sha256((url + '\n' + str(download_revision)).encode()).hexdigest()[:24]
            taskdir = cache / key
            taskdir.mkdir(exist_ok=True)
            manifest = taskdir / 'source.json'
            source = None
            if manifest.exists():
                candidate = (taskdir / json.loads(manifest.read_text(encoding='utf8'))['filename']).resolve()
                if candidate.parent == taskdir.resolve() and candidate.is_file() and candidate.stat().st_size:
                    source = candidate
            if source is None:
                source = _download_reference(url, taskdir, ffmpeg)
                manifest.write_text(json.dumps({'filename':source.name}),encoding='utf8')
        elif local_path.strip():
            source = Path(local_path.strip().strip(chr(34)))
            if not source.is_absolute():
                source = Path(folder_paths.get_input_directory()) / source
            if not source.is_file():
                raise ValueError('找不到本地参考视频：' + str(source))
            _probe(source)
        elif local_video is None:
            raise ValueError('请填写视频链接或本地视频路径。')
        else:
            # Store only this bounded workflow input; no external transmission.
            tmp = cache / 'local'
            tmp.mkdir(exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='source-',suffix='.mp4',dir=tmp)
            os.close(fd)
            source = Path(name)
            local_video.save_to(str(source),format=Types.VideoContainer('mp4'),codec=Types.VideoCodec('h264'),crf=18)
        ephemeral = not url and not local_path.strip()
        try:
            if preserve_source and start_seconds == 0 and duration_seconds == 0 and not ephemeral:
                count, shot_width, shot_height = _probe(source)
                output_width, output_height = _output_size(shot_width, shot_height, 生成长边)
                return io.NodeOutput(InputImpl.VideoFromFile(str(source)), count, output_width, output_height)
            signature = f'{source.resolve()}|{source.stat().st_size}|{source.stat().st_mtime_ns}|{start_seconds}|{duration_seconds}|{reference_long_edge}'
            if preserve_source:
                signature += '|native'
            key = hashlib.sha256(signature.encode()).hexdigest()[:24]
            target_dir = Path(folder_paths.get_output_directory()) / 'Product-Reference' / 'clips'
            target_dir.mkdir(parents=True,exist_ok=True)
            filename = f'reference-{key}.mp4'
            target = target_dir / filename
            requested = int(duration_seconds*24)
            target_frames = 5 + max(0,(requested-5)//17)*17 if duration_seconds > 0 else None
            if not target.exists():
                temporary = target.with_suffix('.tmp.mp4')
                edge = reference_long_edge
                vf = ('fps=24,' if duration_seconds > 0 else '') + f"scale=w='if(gte(iw,ih),{edge},max(32,round(iw/ih*{edge}/32)*32))':h='if(gte(iw,ih),max(32,round(ih/iw*{edge}/32)*32),{edge})':flags=lanczos,setsar=1"
                if preserve_source:
                    vf = ('fps=24,' if duration_seconds > 0 else '') + 'setsar=1'
                logging.info('[ProductReference] Cutting shot: %.2fs, frames=%s',start_seconds,target_frames or 'full')
                _run([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-y','-ss',str(start_seconds),'-i',str(source),
                    *(['-t',str(target_frames/24)] if target_frames is not None else []),'-map','0:v:0','-map','0:a?','-vf',vf,'-filter_threads','2',
                    '-c:v','libx264','-preset','fast','-crf','18','-threads','2','-pix_fmt','yuv420p',
                    '-c:a','aac','-movflags','+faststart',str(temporary)])
                count,_,_ = _probe(temporary)
                if target_frames is not None and count < target_frames:
                    temporary.unlink(missing_ok=True)
                    raise ValueError(f'原视频选段不足 {target_frames/24:.2f} 秒，请提前开始位置或缩短时长。')
                temporary.replace(target)
            count, shot_width, shot_height = _probe(target)
            if target_frames is None:
                target_frames = count
            output_width, output_height = _output_size(shot_width, shot_height, 生成长边)
            return io.NodeOutput(InputImpl.VideoFromFile(str(target)), target_frames, output_width, output_height)
        finally:
            if ephemeral:
                source.unlink(missing_ok=True)


class ProductReferenceExtension(ComfyExtension):
    async def get_node_list(self):
        return [ProductReferenceShot]


async def comfy_entrypoint():
    return ProductReferenceExtension()
