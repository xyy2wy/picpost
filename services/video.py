import glob
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import requests as requests
from tqdm import tqdm


def get_ffmpeg_path():
    """查找可用的 ffmpeg 路径，跨平台。"""
    # 优先使用系统 PATH 中的 ffmpeg
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg

    if platform.system() == "Windows":
        ffmpeg_local_path = "./bin/ffmpeg.exe"
        if os.path.exists(ffmpeg_local_path):
            return os.path.abspath(ffmpeg_local_path)
        return None
    else:
        # macOS/Linux 本地附带的 ffmpeg
        local_path = "./bin/ffmpeg"
        if os.path.exists(local_path):
            return os.path.abspath(local_path)
        return None


def download_ffmpeg(target_path):
    """下载 ffmpeg（仅 Windows 提供预编译包）。"""
    url = "https://file.lsvm.xyz/bin/ffmpeg.exe"
    response = requests.get(url, stream=True)

    # 获取文件总大小（单位：字节）
    total_size = int(response.headers.get('content-length', 0))
    block_size = 8192  # 设置块大小为8KB
    progress_bar = tqdm(total=total_size, unit='B', unit_scale=True)

    with open(target_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=block_size):
            f.write(chunk)
            progress_bar.update(len(chunk))

    # 关闭进度条
    progress_bar.close()

    # 提示下载完成，同时输出 ffmpeg 路径
    print("- 下载完成，ffmpeg 路径为：", target_path)
    print("- 更新新版时请保留此文件，放到对应的位置下，否则无法生成视频。")


def is_integer(n):
    try:
        int(n)  # 尝试将其转换为整数
        return True
    except ValueError:
        return False


def _ensure_ffmpeg():
    """确保 ffmpeg 可用，返回路径或 None。"""
    ffmpeg_path = get_ffmpeg_path()
    if ffmpeg_path:
        return ffmpeg_path

    # 非 Windows 平台没有提供预编译下载，提示用户自行安装
    if platform.system() != "Windows":
        print("- 找不到 ffmpeg。请先安装：")
        print("    macOS:  brew install ffmpeg")
        print("    Linux:  sudo apt install ffmpeg  （或对应发行版的包管理器）")
        return None

    print("- 找不到ffmpeg。正在下载...")
    bin_dir = 'bin'
    if not os.path.exists(bin_dir):
        os.makedirs(bin_dir)
    target = os.path.join(bin_dir, 'ffmpeg.exe')
    download_ffmpeg(target)
    return target


def generate_video(path, gap_time=2):
    if gap_time is None or not is_integer(gap_time):
        gap_time = 2

    ffmpeg_path = _ensure_ffmpeg()
    if not ffmpeg_path:
        return

    current_time = datetime.now().strftime("%Y%m%d%H%M%S")
    output_file = os.path.join(path, f"{current_time}.mp4")

    file_patterns = ['jpg', 'jpeg', 'JPG', 'JPEG']

    # 获取所有匹配的文件路径
    files = []
    for pattern in file_patterns:
        files.extend(glob.glob(f"{path}/*.{pattern}"))

    # 如果没有找到图片，提示用户并返回
    if not files:
        print("- 不存在图片!")
        return

    # 生成文件列表（使用临时文件，避免污染工作目录；用绝对路径并转义单引号）
    concat_fd, concat_path = tempfile.mkstemp(suffix='.txt', prefix='semi_utils_concat_')
    try:
        with os.fdopen(concat_fd, 'w', encoding='utf-8') as f:
            for filename in sorted(files):
                abs_name = os.path.abspath(filename).replace("'", "'\\''")
                f.write(f"file '{abs_name}'\n")

        vf = ("scale=3840:2160:force_original_aspect_ratio=decrease,"
              "pad=3840:2160:(ow-iw)/2:(oh-ih)/2:color=white")
        command = [
            ffmpeg_path, '-y',
            '-f', 'concat', '-safe', '0',
            '-r', f'1/{gap_time}',
            '-i', concat_path,
            '-vf', vf,
            '-c:v', 'libx264', '-r', '24',
            '-pix_fmt', 'yuv420p', '-color_range', '1',
            output_file,
        ]

        process = subprocess.run(command, capture_output=True, encoding='utf-8', errors='ignore')
        if process.returncode == 0:
            print("\ro 视频拼接成功，输出至：" + output_file)
        else:
            print("\r- 视频拼接失败，错误信息：", process.stderr)
            return
    finally:
        if os.path.exists(concat_path):
            os.remove(concat_path)

    # 检查是否存在 bgm.mp3 文件
    bgm_path = os.path.join(path, "bgm.mp3")
    if os.path.exists(bgm_path):
        temp_output_file = os.path.join(path, f"temp_{current_time}.mp4")
        command_bgm = [
            ffmpeg_path, '-y',
            '-i', output_file,
            '-i', bgm_path,
            '-c:v', 'copy', '-c:a', 'aac',
            '-map', '0:v:0', '-map', '1:a:0',
            '-shortest',
            temp_output_file,
        ]

        process = subprocess.Popen(command_bgm, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   encoding='utf-8', errors='ignore')

        # 转动字符显示
        spinning_chars = ['-', '\\', '|', '/']
        idx = 0
        while process.poll() is None:  # 当命令正在执行时
            sys.stdout.write('\r' + spinning_chars[idx % len(spinning_chars)])
            sys.stdout.flush()
            time.sleep(0.1)
            idx += 1

        stdout, stderr = process.communicate()
        if process.returncode == 0:
            print("\ro 视频附加 bgm 成功，输出至：" + temp_output_file)
        else:
            print("\r- 视频附加 bgm 失败，错误信息：", stderr)
    else:
        print("\r- 未找到 bgm.mp3 文件，跳过附加 bgm 步骤。")

    # 提示视频生成成功，告诉用户视频的路径
    print(f"o 视频生成成功，路径为：{output_file}")


if __name__ == '__main__':
    generate_video("./output")
