# M3U8 视频下载工具 (Downloader-mu)

这是一个基于 Python 的多线程 M3U8 (HLS) 视频下载工具。它支持自动解析网页提取 `m3u8` 地址、支持 AES-128 加密流的自动解密、多线程并发下载、智能限速，并在下载完成后自动转换为 MP4 格式。

## 功能特点

- **自动解析**：自动从给定的页面 URL 中正则匹配提取 `m3u8` 地址，并默认选择最高画质。
- **流媒体解密**：原生支持 HLS AES-128 视频切片解密。
- **多线程并发**：默认采用 8 线程并发下载切片，充分利用带宽抵消网络延迟。
- **智能限速**：内置基于视频时长的全局倍速限制（默认 2.0x 倍速），避免触发服务端的频繁请求限制。
- **格式转换**：下载完成后自动调用 `ffmpeg` 将 `.ts` 转换为更通用的 `.mp4` 格式。
- **批量下载**：支持在命令行输入多个 URL，或者通过读取 `.txt` 文件批量进行下载任务，并清晰展示任务进度。

## 环境与依赖安装

### 1. 系统依赖包: FFmpeg (必须)
在视频切片下载完成并合并后，脚本需要依赖 `ffmpeg` 进行无损转封装。如果不安装此工具，视频依然可以下载完毕（保存为 `.ts` 格式），但无法自动转为 `.mp4`。

- **macOS:**
  ```bash
  brew install ffmpeg
  ```
- **Ubuntu / Debian (Linux):**
  ```bash
  sudo apt update
  sudo apt install ffmpeg
  ```
- **Windows:**
  请前往 FFmpeg 官方网站 下载编译好的可执行文件，并将其所在的 `bin` 目录添加到系统的环境变量（Path）中。

### 2. Python 依赖库
确保您的系统中已安装 Python 3 环境。然后通过 `pip` 安装必要的 Python 库（其中 `pycryptodome` 库提供了 `Crypto.Cipher.AES` 的支持）：

```bash
pip install requests m3u8 pycryptodome
```

## 使用方法

### 1. 下载单个或多个视频（通过命令行参数）
您可以直接在命令行后追加任意数量的视频落地页 URL，程序会依次处理。

```bash
python3 downloader-mu.py <视频落地页URL1> [视频落地页URL2] ...
```

### 2. 批量下载（通过 txt 文本文件）
对于大量的下载需求，建议将所有想要下载的视频 URL 保存到一个 `.txt` 文件中，每行一个链接（空行或以 `#` 开头的行会被自动忽略作为注释处理）。

假设您有一个 `urls.txt` 文件内容如下：
```text
https://example.com/video/12345/
# 这是一个备用视频
https://example.com/video/67890/
```
运行以下命令开始批量下载：
```bash
python3 downloader-mu.py urls.txt
```