import requests
import m3u8
import os
import time
import re
from bs4 import BeautifulSoup
from Crypto.Cipher import AES

def get_m3u8_url(page_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
    }
    response = requests.get(page_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Try to find m3u8 in scripts
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string:
            match = re.search(r"var hlsUrl = '(.*?)';", script.string)
            if match:
                return match.group(1)
            
    # Fallback to searching the whole text
    match = re.search(r"https://[^\"']+\.m3u8", response.text)
    if match:
        return match.group(0)
    
    return None

def download_video(url, rate_limit_factor=2.0):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36'
    }
    m3u8_url = get_m3u8_url(url)
    if not m3u8_url:
        print("Could not find m3u8 URL")
        return

    print(f"Found m3u8 URL: {m3u8_url}")
    
    base_url = m3u8_url.rsplit('/', 1)[0]
    
    # Load m3u8 with headers
    m3u8_response = requests.get(m3u8_url, headers=headers)
    m3u8_obj = m3u8.loads(m3u8_response.text, uri=m3u8_url)
    
    if m3u8_obj.is_variant:
        # Pick the highest bandwidth variant
        variant = max(m3u8_obj.playlists, key=lambda p: p.stream_info.bandwidth)
        m3u8_url = variant.absolute_uri
        m3u8_response = requests.get(m3u8_url, headers=headers)
        m3u8_obj = m3u8.loads(m3u8_response.text, uri=m3u8_url)
        base_url = m3u8_url.rsplit('/', 1)[0]
        print(f"Using variant: {m3u8_url}")

    segments = m3u8_obj.segments
    media_sequence = m3u8_obj.media_sequence or 0
    key_info = m3u8_obj.keys[0] if m3u8_obj.keys else None
    
    if key_info:
        print(f"Stream is encrypted. Key URI: {key_info.uri}")
        key_url = key_info.uri
        if not key_url.startswith('http'):
            key_url = f"{base_url}/{key_url}"
        
        key_response = requests.get(key_url, headers=headers)
        key = key_response.content
        
    import subprocess

    ts_name = url.strip('/').split('/')[-1] + ".ts"
    mp4_name = url.strip('/').split('/')[-1] + ".mp4"
    print(f"正在保存中间文件: {ts_name}")
    
    downloaded_segments = 0
    try:
        with open(ts_name, 'wb') as f:
            for i, segment in enumerate(segments):
                # ... (rest of the download loop remains the same)
                current_sequence = media_sequence + i
                seg_url = segment.absolute_uri
                if not seg_url.startswith('http'):
                    seg_url = f"{base_url}/{seg_url}"
                
                duration = segment.duration
                target_time = duration / rate_limit_factor
                
                start_time = time.time()
                
                try:
                    seg_response = requests.get(seg_url, headers=headers, timeout=15)
                    seg_response.raise_for_status()
                    data = seg_response.content
                except Exception as e:
                    print(f"\n错误下载切片 {i+1}: {e}")
                    continue
                
                if key_info:
                    iv = key_info.iv
                    if not iv:
                        iv = current_sequence.to_bytes(16, byteorder='big')
                    elif isinstance(iv, str) and iv.startswith('0x'):
                        iv = bytes.fromhex(iv[2:])
                    
                    try:
                        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
                        data = cipher.decrypt(data)
                    except Exception as e:
                        print(f"\n解密错误 {i+1}: {e}")
                
                f.write(data)
                downloaded_segments += 1
                
                percent = (i + 1) / len(segments) * 100
                elapsed = time.time() - start_time
                print(f"进度: {percent:6.2f}% | 切片 {i+1}/{len(segments)} | 时长: {duration}s | 限速中...", end='\r')
                
                if elapsed < target_time:
                    time.sleep(target_time - elapsed)
                    
    except KeyboardInterrupt:
        print("\n下载被用户中断。")
    
    if downloaded_segments > 0:
        print(f"\n下载完成。共处理 {downloaded_segments} 个切片。")
        print(f"正在转换为 MP4...")
        
        try:
            # 使用 ffmpeg 进行无损转换 (封装格式转换)
            result = subprocess.run(
                ['ffmpeg', '-y', '-i', ts_name, '-c', 'copy', mp4_name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"转换成功: {mp4_name}")
                # 转换成功后删除中间的 .ts 文件
                os.remove(ts_name)
            else:
                print(f"\nFFmpeg 转换失败。请确保系统中已安装 ffmpeg。")
                print(f"保留原始 TS 文件: {ts_name}")
        except FileNotFoundError:
            print(f"\n未找到 FFmpeg 命令行工具，无法自动转换。")
            print(f"请手动运行: ffmpeg -i {ts_name} -c copy {mp4_name}")
            print(f"当前文件已保存为: {ts_name}")
    else:
        print("\n未下载任何内容。")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 downloader.py <url>")
    else:
        download_video(sys.argv[1])
