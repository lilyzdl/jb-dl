import requests
import m3u8
import os
import time
import re
import subprocess
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from Crypto.Cipher import AES


def get_m3u8_url(page_url, session=None):
    # 如果传入的直接就是 m3u8 或 mp4 链接，则直接返回
    if '.m3u8' in page_url or '.mp4' in page_url:
        return page_url

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36',
        'Referer': page_url
    }
    if not session:
        session = requests.Session()
        
    try:
        res = session.get(page_url, headers=headers, timeout=10)
        text = res.text

        # 1. 尝试查找常用的 hlsUrl 变量
        match = re.search(r"var hlsUrl\s*=\s*['\"](.*?)['\"];", text)
        if match:
            return match.group(1)
            
        # 2. 尝试查找 player 初始化的 source 变量 (m3u8/mp4)
        match = re.search(r"source\s*:\s*['\"](.*?)['\"]", text)
        if match and ('.m3u8' in match.group(1) or '.mp4' in match.group(1)):
            return match.group(1).replace('\\/', '/')
            
        # 3. 尝试查找 <source src="...m3u8/mp4">
        match = re.search(r"<source[^>]+src=['\"]([^'\"]+\.(?:m3u8|mp4)[^'\"]*)['\"]", text)
        if match:
            return match.group(1).replace('\\/', '/')

        # 4. 遍历所有 iframe 查找嵌套的播放器（放宽匹配条件）
        iframe_matches = re.findall(r"<iframe[^>]+src=['\"]([^'\"]+)['\"]", text)
        for embed_url in iframe_matches:
            if any(x in embed_url for x in ['ads', 'banner', 'pop']): continue
            
            if embed_url.startswith('//'):
                embed_url = 'https:' + embed_url
            elif embed_url.startswith('/'):
                from urllib.parse import urlparse
                parsed = urlparse(page_url)
                embed_url = f"{parsed.scheme}://{parsed.netloc}{embed_url}"
            elif not embed_url.startswith('http'):
                continue
                
            try:
                embed_res = session.get(embed_url, headers=headers, timeout=10)
                embed_text = embed_res.text
                
                # 在 iframe 源码中查找 m3u8 或 mp4
                match = re.search(r"source\s*:\s*['\"](.*?)['\"]", embed_text)
                if match and ('.m3u8' in match.group(1) or '.mp4' in match.group(1)):
                    return match.group(1).replace('\\/', '/')
                    
                match = re.search(r"['\"](https?://[^'\"]+\.(?:m3u8|mp4)[^'\"]*)['\"]", embed_text)
                if match:
                    return match.group(1).replace('\\/', '/')
            except:
                continue

        # 5. 终极 Fallback: 全局正则匹配任何 http(s) 开头且包含 .m3u8 或 .mp4 的链接
        match = re.search(r"https?://[^\"']+\.(?:m3u8|mp4)[^\"']*", text)
        return match.group(0).replace('\\/', '/') if match else None

    except Exception as e:
        print(f"解析页面失败: {e}")
        return None

class JableDownloader:
    def __init__(self, url, factor=2.0):
        self.url = url
        self.factor = factor
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36',
            'Referer': self.url
        })
        self.start_time = None
        self.total_video_duration_downloaded = 0
        self.lock = threading.Lock()

    def download_segment(self, seg_info):
        idx, seg_url, duration, key, key_info, media_sequence, output_path = seg_info

        # 恢复下载检查：如果切片文件已存在且不为空，则跳过
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            with self.lock:
                self.total_video_duration_downloaded += duration
            return idx, True

        # 全局限速检查：如果当前下载速度超过了设定倍速，则休眠
        while True:
            elapsed = time.time() - self.start_time
            if elapsed <= 0:
                break
            with self.lock:
                current_rate = self.total_video_duration_downloaded / elapsed
            if current_rate > self.factor:
                time.sleep(0.1)
            else:
                break

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 下载切片
                res = self.session.get(seg_url, timeout=20)
                res.raise_for_status()
                data = res.content

                # 解密
                if key:
                    iv = key_info.iv
                    if not iv:
                        # 使用序列号作为 IV
                        iv = (media_sequence + idx).to_bytes(16, byteorder='big')
                    elif isinstance(iv, str) and iv.startswith('0x'):
                        iv = bytes.fromhex(iv[2:])

                    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
                    data = cipher.decrypt(data)

                with open(output_path, 'wb') as f:
                    f.write(data)

                with self.lock:
                    self.total_video_duration_downloaded += duration
                return idx, True
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"\n切片 {idx} 下载超时或失败: {e}，正在重试 ({attempt + 1}/{max_retries})...")
                    time.sleep(2)  # 等待 2 秒后重试
                else:
                    print(f"\n切片 {idx} 彻底下载失败: {e}")
                    return idx, False
        return idx, False

    def download_direct_mp4(self, url, title_slug):
        mp4_name = f"{title_slug}.mp4"
        print(f"\n检测到直接的 MP4 链接，采用流式断点续传下载: {url}")
        
        headers = self.session.headers.copy()
        downloaded_size = 0
        if os.path.exists(mp4_name):
            downloaded_size = os.path.getsize(mp4_name)
            headers['Range'] = f'bytes={downloaded_size}-'
            print(f"发现已存在的文件 {mp4_name}，已下载 {downloaded_size / (1024*1024):.2f} MB，正在尝试断点续传...")

        try:
            with self.session.get(url, headers=headers, stream=True, timeout=15) as res:
                res.raise_for_status()
                
                total_size = int(res.headers.get('content-length', 0))
                if res.status_code == 206: # 服务器支持断点续传 (Partial Content)
                    total_size += downloaded_size
                elif res.status_code == 200: # 服务器不支持断点续传，从头开始
                    downloaded_size = 0
                else:
                    print(f"服务器返回异常状态码: {res.status_code}")
                    return

                print(f"总文件大小预计: {total_size / (1024*1024):.2f} MB")
                
                mode = 'ab' if downloaded_size > 0 else 'wb'
                with open(mp4_name, mode) as f:
                    start_time = time.time()
                    current_size = downloaded_size
                    for chunk in res.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
                            current_size += len(chunk)
                            
                            if total_size > 0:
                                percent = current_size / total_size * 100
                                elapsed = time.time() - start_time
                                speed = (current_size - downloaded_size) / elapsed / (1024*1024) if elapsed > 0 else 0
                                print(f"进度: {percent:6.2f}% | 已下载: {current_size/(1024*1024):.2f}MB/{total_size/(1024*1024):.2f}MB | 速度: {speed:.2f} MB/s", end='\r')
                            else:
                                print(f"已下载: {current_size/(1024*1024):.2f} MB", end='\r')
                                
            print(f"\n\n🎉 MP4 文件下载完成: {mp4_name}")
        except Exception as e:
            print(f"\n\n❌ MP4 下载出错: {e}")
            print(f"临时文件已保留，可重新运行相同命令继续下载。")

    def run(self):
        print(f"正在解析/请求: {self.url}")
        
        # 尝试提取网页标题作为下载后的文件名，解决乱码数字问题
        title_slug = self.url.split('?')[0].strip('/').split('/')[-1]
        title_slug = re.sub(r'\.(m3u8|mp4).*$', '', title_slug) # 若直传 m3u8/mp4 则去掉后缀

        if not ('.m3u8' in self.url or '.mp4' in self.url):
            try:
                res = self.session.get(self.url, timeout=10)
                match = re.search(r"<title>(.*?)</title>", res.text, re.IGNORECASE)
                if match:
                    raw_title = match.group(1).strip()
                    # 过滤操作系统的非法文件名字符
                    clean_title = re.sub(r'[\\/*?:"<>|]', "", raw_title)
                    if clean_title:
                        title_slug = clean_title
            except:
                pass

        m3u8_url = get_m3u8_url(self.url, self.session)
        if not m3u8_url:
            print("\n❌ 未能自动解析出 m3u8 或 mp4 视频地址！")
            print("【原因分析】:")
            print("有些网站（如 avjoy）必须在页面上“点击播放按钮”后，才会通过前端 JS 动态向服务器请求真实的视频地址。这种情况下 Python 脚本无法自动获取。")
            print("\n【💡 终极解决办法】:")
            print("1. 在浏览器打开此视频页面，按 F12 键打开“开发者工具”，切换到 Network (网络) 面板。")
            print("2. 点击网页上的播放按钮，然后在 Network 面板的过滤框中输入 m3u8 或 mp4。")
            print("3. 找到并复制抓包出来的那个真实的视频链接。")
            print("4. 直接将该链接传给本脚本执行：")
            print(f"   python3 downloader-mu-resume.py \"https://.../video.mp4\"")
            return
            
        if '.mp4' in m3u8_url and not '.m3u8' in m3u8_url:
            self.download_direct_mp4(m3u8_url, title_slug)
            return

        res = self.session.get(m3u8_url)
        m3u8_obj = m3u8.loads(res.text, uri=m3u8_url)
        
        if m3u8_obj.is_variant:
            variant = max(m3u8_obj.playlists, key=lambda p: p.stream_info.bandwidth)
            m3u8_url = variant.absolute_uri
            print(f"选择最高画质: {m3u8_url}")
            m3u8_obj = m3u8.loads(self.session.get(m3u8_url).text, uri=m3u8_url)

        segments = m3u8_obj.segments
        base_url = m3u8_url.rsplit('/', 1)[0]
        
        # 获取解密 Key
        key_info = m3u8_obj.keys[0] if m3u8_obj.keys else None
        key = None
        if key_info:
            key_uri = key_info.uri
            key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
            key = self.session.get(key_url).content
            print("检测到加密流，已获取解密密钥。")

        ts_name = f"{title_slug}.ts"
        mp4_name = f"{title_slug}.mp4"
        
        temp_dir = f"{title_slug}_temp"
        os.makedirs(temp_dir, exist_ok=True)

        print(f"开始多线程限速下载 (目标倍速: {self.factor}x)...")
        print(f"临时文件将保存在: {temp_dir}")
        self.start_time = time.time()
        
        # 准备任务列表
        media_sequence = m3u8_obj.media_sequence or 0
        tasks = []
        for i, seg in enumerate(segments):
            seg_url = seg.absolute_uri
            output_path = os.path.join(temp_dir, f"{i}.ts")
            tasks.append((i, seg_url, seg.duration, key, key_info, media_sequence, output_path))

        failed_segments = []
        try:
            with ThreadPoolExecutor(max_workers=8) as executor:
                # executor.map 会保持输入列表的顺序返回结果
                for idx, success in executor.map(self.download_segment, tasks):
                    if not success:
                        failed_segments.append(idx)

                    # 打印进度和实时倍速
                    percent = (idx + 1) / len(segments) * 100
                    elapsed = time.time() - self.start_time
                    with self.lock:
                        real_time_factor = self.total_video_duration_downloaded / elapsed if elapsed > 0 else 0
                    print(f"进度: {percent:6.2f}% | 切片 {idx+1}/{len(segments)} | 实时倍速: {real_time_factor:.2f}x", end='\r')

            if failed_segments:
                print(f"\n\n下载未完成。有 {len(failed_segments)} 个切片下载失败。")
                print(f"失败的切片编号: {sorted(failed_segments)}")
                print(f"临时文件已保存在 {temp_dir}，请重新运行此命令以继续下载。")
                return

            print(f"\n\n所有切片下载完成！正在合并文件...")
            with open(ts_name, 'wb') as f_out:
                for i in range(len(segments)):
                    segment_path = os.path.join(temp_dir, f"{i}.ts")
                    with open(segment_path, 'rb') as f_in:
                        f_out.write(f_in.read())
            print(f"合并完成: {ts_name}")

            # 调用 FFmpeg 转换为 MP4
            print("正在转换为 MP4 格式...")
            try:
                cmd = ['ffmpeg', '-y', '-i', ts_name, '-c', 'copy', mp4_name]
                # 隐藏 ffmpeg 的详细输出，只在出错时显示
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                if result.returncode == 0:
                    print(f"转换成功: {mp4_name}")
                    os.remove(ts_name)
                    print(f"正在清理临时文件: {temp_dir}")
                    shutil.rmtree(temp_dir)
                else:
                    print(f"\nFFmpeg 转换失败: {result.stderr.decode()}")
                    print(f"中间文件已保留: {ts_name}")
            except FileNotFoundError:
                print(f"\n系统中未找到 ffmpeg，请手动运行命令转换: ffmpeg -i {ts_name} -c copy {mp4_name}")
                print(f"当前已保存为: {ts_name}")

        except KeyboardInterrupt:
            print("\n\n下载已由用户中断。临时文件已保留，可重新运行命令继续。")
        except Exception as e:
            print(f"\n运行出错: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 downloader-mu-resume.py <视频落地页URL1> [URL2] ... 或 <urls.txt>")
    else:
        urls = []
        # 1. 批量输入：支持命令行传入多个 URL，或者直接传入一个包含 URL 列表的 .txt 文件
        for arg in sys.argv[1:]:
            if os.path.isfile(arg) and arg.endswith('.txt'):
                with open(arg, 'r', encoding='utf-8') as f:
                    for line in f:
                        clean_url = line.strip()
                        if clean_url and not clean_url.startswith('#'):
                            urls.append(clean_url)
            else:
                urls.append(arg)

        total = len(urls)
        if total == 0:
            print("未找到任何有效链接，请检查输入。")
            sys.exit(0)

        try:
            for i, current_url in enumerate(urls, 1):
                # 2. 进度展示：在每次开始下载前清晰打印当前下载进度和对应的 URL
                print(f"\n{'='*50}")
                print(f"任务进度: [{i}/{total}]")
                print(f"当前正在下载: {current_url}")
                print(f"{'='*50}")
                
                downloader = JableDownloader(current_url, factor=2.0)
                downloader.run()
                
            print("\n🎉 所有批量下载任务已完成！")
        except KeyboardInterrupt:
            print("\n批量下载任务已被用户强制停止。")