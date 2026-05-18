import requests
import m3u8
import os
import time
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from Crypto.Cipher import AES


def get_m3u8_url(page_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
    }
    try:
        res = requests.get(page_url, headers=headers, timeout=10)
        match = re.search(r"var hlsUrl = '(.*?)';", res.text)
        if match:
            return match.group(1)
        # Fallback to general m3u8 search
        match = re.search(r"https://[^\"']+\.m3u8", res.text)
        return match.group(0) if match else None
    except Exception as e:
        print(f"解析页面失败: {e}")
        return None

class JableDownloader:
    def __init__(self, url, factor=2.0):
        self.url = url
        self.factor = factor
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
        })
        self.start_time = None
        self.total_video_duration_downloaded = 0
        self.lock = None # ThreadPoolExecutor's map preserves order, so we don't strictly need a lock for file writing

    def download_segment(self, seg_info):
        idx, seg_url, duration, key, key_info, media_sequence = seg_info
        
        # 全局限速检查：如果当前下载速度超过了设定倍速，则休眠
        while True:
            elapsed = time.time() - self.start_time
            if elapsed <= 0:
                break
            current_rate = self.total_video_duration_downloaded / elapsed
            if current_rate > self.factor:
                time.sleep(0.1)
            else:
                break

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
            
            # 更新已下载的视频时长（用于限速计算）
            self.total_video_duration_downloaded += duration
            return idx, data
        except Exception as e:
            print(f"\n切片 {idx} 下载失败: {e}")
            return idx, None

    def run(self):
        print(f"正在解析页面: {self.url}")
        m3u8_url = get_m3u8_url(self.url)
        if not m3u8_url:
            print("未找到 m3u8 地址，请检查 URL 是否正确。")
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

        slug = self.url.strip('/').split('/')[-1]
        ts_name = f"{slug}.ts"
        mp4_name = f"{slug}.mp4"
        
        print(f"开始多线程限速下载 (目标倍速: {self.factor}x)...")
        self.start_time = time.time()
        
        # 准备任务列表
        media_sequence = m3u8_obj.media_sequence or 0
        tasks = []
        for i, seg in enumerate(segments):
            seg_url = seg.absolute_uri
            tasks.append((i, seg_url, seg.duration, key, key_info, media_sequence))

        # 使用线程池并发下载
        # executor.map 会保持输入列表的顺序返回结果
        try:
            with open(ts_name, 'wb') as f:
                # 建议使用 5-10 个线程，足以抵消网络延迟并达到 2x 限速
                with ThreadPoolExecutor(max_workers=8) as executor:
                    for idx, data in executor.map(self.download_segment, tasks):
                        if data:
                            f.write(data)
                        
                        # 打印进度和实时倍速
                        percent = (idx + 1) / len(segments) * 100
                        elapsed = time.time() - self.start_time
                        real_time_factor = self.total_video_duration_downloaded / elapsed if elapsed > 0 else 0
                        print(f"进度: {percent:6.2f}% | 切片 {idx+1}/{len(segments)} | 实时倍速: {real_time_factor:.2f}x", end='\r')

            print(f"\n下载完成！共处理 {len(segments)} 个切片。")
            
            # 调用 FFmpeg 转换为 MP4
            print("正在转换为 MP4 格式...")
            try:
                cmd = ['ffmpeg', '-y', '-i', ts_name, '-c', 'copy', mp4_name]
                # 隐藏 ffmpeg 的详细输出，只在出错时显示
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                if result.returncode == 0:
                    print(f"转换成功: {mp4_name}")
                    os.remove(ts_name)
                else:
                    print(f"\nFFmpeg 转换失败: {result.stderr.decode()}")
                    print(f"中间文件已保留: {ts_name}")
            except FileNotFoundError:
                print(f"\n系统中未找到 ffmpeg，请手动运行命令转换: ffmpeg -i {ts_name} -c copy {mp4_name}")
                print(f"当前已保存为: {ts_name}")

        except KeyboardInterrupt:
            print("\n下载已由用户中断。")
            raise
        except Exception as e:
            print(f"\n运行出错: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 downloader-mu.py <视频落地页URL1> [URL2] ... 或 <urls.txt>")
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
