import os
import sys
import re
import wave
import time
import asyncio
import contextlib
from pathlib import Path
import mlx_whisper

# ==========================================
# 辅助函数：时间格式化
# ==========================================
def format_time_srt(seconds: float) -> str:
    """将秒数转化为 SRT 标准时间戳 (00:00:00,000)"""
    hours, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    millis = int((secs - int(secs)) * 1000)
    return f"{int(hours):02d}:{int(mins):02d}:{int(secs):02d},{millis:03d}"

# ==========================================
# 进度条黑科技：劫持标准输出流
# ==========================================
class WhisperProgressStream:
    def __init__(self, total_duration):
        self.total_duration = max(total_duration, 1.0)
        self.current_progress = 0
        self.original_stdout = sys.stdout
        self.start_time = time.time()
        self._print_bar()

    def write(self, text):
        # 使用正则捕捉 Whisper 打印的结束时间，例如 "[00:00.000 --> 00:05.123]"
        m = re.search(r'-->\s*(?:(\d+):)?(\d{2}):(\d{2})\.\d{3}', text)
        if m:
            h = int(m.group(1)) if m.group(1) else 0
            mins = int(m.group(2))
            secs = int(m.group(3))
            end_time = h * 3600 + mins * 60 + secs
            
            # 计算百分比
            progress = min(100, int((end_time / self.total_duration) * 100))
            if progress > self.current_progress:
                self.current_progress = progress
                self._print_bar()

    def flush(self):
        pass # 必须实现 flush 方法以防报错
        
    def _print_bar(self):
        bar_len = 40
        filled_len = int(bar_len * self.current_progress // 100)
        bar = '█' * filled_len + '░' * (bar_len - filled_len)
        
        elapsed = time.time() - self.start_time
        if self.current_progress > 0:
            total_est = elapsed / (self.current_progress / 100.0)
            remaining = max(0, total_est - elapsed)
        else:
            remaining = 0
            
        rem_m, rem_s = divmod(int(remaining), 60)
        el_m, el_s = divmod(int(elapsed), 60)
        
        # 使用 \r 回车符实现原地刷新，不产生新行
        self.original_stdout.write(f'\r🧠 AI 听写进度: |{bar}| {self.current_progress}% [已耗时: {el_m:02d}:{el_s:02d} < 预估剩余: {rem_m:02d}:{rem_s:02d}]')
        self.original_stdout.flush()

    def close(self):
        self.current_progress = 100
        self._print_bar()
        self.original_stdout.write('\n') # 结束后换行
        self.original_stdout.flush()

# ==========================================
# 核心处理管线
# ==========================================
async def extract_audio(media_path: Path, audio_path: Path) -> bool:
    """调用 FFmpeg 极速剥离/重采样为 16000Hz 纯净音频"""
    print(f"✂️ 正在剥离并重采样纯净单声道音频 (16kHz)...")
    cmd = [
        "ffmpeg", "-y", "-i", str(media_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_path)
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await process.communicate()
    return process.returncode == 0

def transcribe_audio(audio_path: Path, model_name: str) -> list:
    """唤醒 MLX Whisper 提取带字级时间戳的生肉"""
    print(f"🚀 唤醒 Mac 统一内存与 NPU 算力...")
    print(f"📦 加载模型 [{model_name}] ...")
    
    # 1. 毫秒级读取音频总时长
    with wave.open(str(audio_path), 'rb') as f:
        duration = f.getnframes() / float(f.getframerate())
        
    print(f"⏱️ 提取到总音频时长: {duration/60:.2f} 分钟。开始执行全场听译：")
    
    # 2. 挂载输出拦截器
    progress_stream = WhisperProgressStream(duration)
    
    try:
        # 强制将标准输出 (stdout) 重定向到我们的拦截器里
        with contextlib.redirect_stdout(progress_stream):
            result = mlx_whisper.transcribe(
                str(audio_path),
                path_or_hf_repo=model_name,
                fp16=True,
                word_timestamps=True, # 🔪 开启手术刀级字级对齐
                verbose=True          # 💡 必须开启，这样拦截器才能捕捉到文本并计算进度
            )
    finally:
        progress_stream.close()
    
    segments = result.get('segments', [])
    
    # 🧬 核心修复：修剪静音脂肪
    for seg in segments:
        words = seg.get('words', [])
        if words:
            seg['start'] = words[0]['start']
            seg['end'] = words[-1]['end']
            
    return segments

# ==========================================
# 交互主程序
# ==========================================
async def main():
    print("="*60)
    print("🎙️ GloBot 演唱会全场生肉粗轴提取器 (带动态进度条版)")
    print("="*60)
    
    input_path_str = input("\n📥 请拖入需要提取的演唱会视频(MP4/MOV)或音频文件: ").strip().strip("'").strip('"')
    input_path = Path(input_path_str)
    
    if not input_path.exists():
        print("❌ 找不到该文件，请检查路径。")
        return

    default_out_dir = input_path.parent
    out_dir_str = input(f"💾 请输入 .srt 保存的文件夹路径 (直接回车默认保存在源文件同级目录): ").strip().strip("'").strip('"')
    
    out_dir = Path(out_dir_str) if out_dir_str else default_out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    
    output_srt = out_dir / f"{input_path.stem}_raw.srt"
    temp_audio = out_dir / f"temp_globot_{input_path.stem}.wav"
    
    start_time = time.time()
    
    success = await extract_audio(input_path, temp_audio)
    if not success:
        print("❌ 音频剥离失败，请确保您的电脑已安装 FFmpeg。")
        return

    model_name = "mlx-community/whisper-large-v3-turbo"
    try:
        segments = await asyncio.to_thread(transcribe_audio, temp_audio, model_name)
    except Exception as e:
        print(f"\n❌ 转录发生致命错误: {e}")
        if temp_audio.exists(): temp_audio.unlink()
        return
        
    if not segments:
        print("⚠️ 未提取到任何有效语音内容。")
    else:
        print(f"📝 正在格式化并对齐 {len(segments)} 句台词的时间轴...")
        srt_lines = []
        for i, seg in enumerate(segments):
            start_str = format_time_srt(seg['start'])
            end_str = format_time_srt(seg['end'])
            text = seg['text'].strip()
            srt_lines.append(f"{i + 1}\n{start_str} --> {end_str}\n{text}\n")
            
        with open(output_srt, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
            
        cost = time.time() - start_time
        print(f"\n🎉 提取完美收工！总耗时: {cost:.2f} 秒。")
        print(f"✅ 演唱会生肉 SRT 已保存至: {output_srt}")
        
    if temp_audio.exists():
        temp_audio.unlink()
        print("🧹 临时音频缓存已清理。")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 用户手动终止了提取进程。")