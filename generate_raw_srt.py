import os
import asyncio
import time
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
    print(f"🧠 加载模型 [{model_name}] (这可能需要较长时间，请去喝杯咖啡☕️)...")
    
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model_name,
        fp16=True,
        word_timestamps=True # 🔪 开启手术刀级字级对齐
    )
    
    segments = result.get('segments', [])
    
    # 🧬 核心修复：修剪静音脂肪
    for seg in segments:
        words = seg.get('words', [])
        if words:
            # 强制将句子的出现时间，绑定在“第一个字”刚发音的那一瞬间
            seg['start'] = words[0]['start']
            # 强制将句子的消失时间，绑定在“最后一个字”说完的那一瞬间
            seg['end'] = words[-1]['end']
            
    return segments

# ==========================================
# 交互主程序
# ==========================================
async def main():
    print("="*60)
    print("🎙️ GloBot 演唱会全场生肉粗轴提取器 (MLX 极速版)")
    print("="*60)
    
    # 1. 获取输入文件
    input_path_str = input("\n📥 请拖入需要提取的演唱会视频(MP4/MOV)或音频文件: ").strip().strip("'").strip('"')
    input_path = Path(input_path_str)
    
    if not input_path.exists():
        print("❌ 找不到该文件，请检查路径。")
        return

    # 2. 获取输出路径
    default_out_dir = input_path.parent
    out_dir_str = input(f"💾 请输入 .srt 保存的文件夹路径 (直接回车默认保存在源文件同级目录): ").strip().strip("'").strip('"')
    
    out_dir = Path(out_dir_str) if out_dir_str else default_out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    
    output_srt = out_dir / f"{input_path.stem}_raw.srt"
    temp_audio = out_dir / f"temp_globot_{input_path.stem}.wav"
    
    start_time = time.time()
    
    # 3. 剥离音频 (不管是视频还是音频，统一用 FFmpeg 洗成标准 16k wav 防断连)
    success = await extract_audio(input_path, temp_audio)
    if not success:
        print("❌ 音频剥离失败，请确保您的电脑已安装 FFmpeg。")
        return

    # 4. 执行 AI 转录
    model_name = "mlx-community/whisper-large-v3-turbo" # 默认使用大模型 Turbo 版
    try:
        # 使用 to_thread 防止阻塞事件循环
        segments = await asyncio.to_thread(transcribe_audio, temp_audio, model_name)
    except Exception as e:
        print(f"\n❌ 转录发生致命错误: {e}")
        if temp_audio.exists(): temp_audio.unlink()
        return
        
    if not segments:
        print("⚠️ 未提取到任何有效语音内容。")
    else:
        # 5. 格式化导出
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
        
    # 6. 清理现场
    if temp_audio.exists():
        temp_audio.unlink()
        print("🧹 临时音频缓存已清理。")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 用户手动终止了提取进程。")