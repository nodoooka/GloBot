import os
import asyncio
import logging
from pathlib import Path
import sys

# 将项目根目录加入系统路径
sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings
import mlx_whisper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def extract_audio(video_path: Path, audio_path: Path) -> bool:
    """调用 FFmpeg 极速剥离纯净音频"""
    logger.info(f"✂️ 正在从视频中剥离纯净音频: {video_path.name} ...")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio_path)
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    
    if process.returncode == 0:
        logger.info("✅ 音频剥离成功！")
        return True
    else:
        logger.error(f"❌ 音频剥离失败: {stderr.decode().strip()}")
        return False

async def transcribe_audio(audio_path: Path) -> dict:
    """唤醒 MLX Whisper 提取日语时间轴"""
    model_name = settings.media_engine.whisper_model
    logger.info(f"🚀 正在唤醒 MLX 神经网络算力 (模型: {model_name}) ...")
    
    try:
        # 💡 核心开启：word_timestamps=True，强制模型追踪每一个字的精确发音时间
        # 因为 MLX 的调用是同步的，我们在 asyncio 里用 to_thread 防止阻塞主循环
        result = await asyncio.to_thread(
            mlx_whisper.transcribe,
            str(audio_path),
            path_or_hf_repo=model_name,
            fp16=True,
            word_timestamps=True # 🔪 手术刀级对齐开关
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
                
        logger.info(f"✅ 听译与词级对齐完成！共识别到 {len(segments)} 句话。")
        return result
        
    except Exception as e:
        logger.error(f"❌ 听译引擎崩溃: {e}")
        return {'segments': []}

# ==========================================
# 🧪 单点测试
# ==========================================
if __name__ == "__main__":
    # 填入一个测试视频
    test_video = Path("/Users/tgmesmer/GloBot/GloBot_Data/iLiFE/media/ilife_official/2025556349686583620_video.mp4")
    test_audio = test_video.parent / "temp_test_audio.wav"
    
    async def run_test():
        if await extract_audio(test_video, test_audio):
            result = await transcribe_audio(test_audio)
            print("\n🎯 词级修复后的精准时间轴：")
            for seg in result.get('segments', []):
                print(f"[{seg['start']:.2f}s -> {seg['end']:.2f}s] {seg['text']}")
            if test_audio.exists():
                test_audio.unlink()
                
    if test_video.exists():
        asyncio.run(run_test())
    else:
        print("❌ 找不到测试视频！")