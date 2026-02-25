import os
import shutil
import asyncio
import logging
from pathlib import Path
import sys
import re

sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings
from Bot_Media.audio_transcriber import extract_audio, transcribe_audio
from Bot_Media.video_ocr import extract_video_text
from Bot_Media.llm_translator import translate_batch 

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def format_time_srt(seconds: float) -> str:
    hours, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    millis = int((secs - int(secs)) * 1000)
    return f"{int(hours):02d}:{int(mins):02d}:{int(secs):02d},{millis:03d}"

async def process_with_ai(source_file: Path, output_file: Path):
    logger.info(f"🧠 [AI 引擎启动] 解析中: {source_file.name}")
    work_dir = source_file.parent
    audio_file = work_dir / f"temp_audio_{source_file.stem}.wav"
    srt_file = work_dir / f"temp_subs_{source_file.stem}.srt"

    try:
        # 1. 👁️ 视觉(NPU) + 👂 听觉(FFmpeg) 并发提取
        ocr_task = asyncio.create_task(extract_video_text(source_file))
        audio_task = asyncio.create_task(extract_audio(source_file, audio_file))
        ocr_results, audio_success = await asyncio.gather(ocr_task, audio_task)
        
        if not audio_success: return

        # 2. 🧠 音频转录 (MLX Whisper)
        whisper_results = await transcribe_audio(audio_file)
        segments = whisper_results.get('segments', [])
        if not segments:
            shutil.copy2(source_file, output_file)
            return

        # 3. 🧬 全剧本打包翻译
        logger.info(f"🧬 开始双模态上下文融合，打包发送给 AI 翻译中...")
        cn_texts = await translate_batch(segments, ocr_results)
        
        # 4. 📝 组装并写入本地 SRT 字幕文件 (纯净单语版)
        srt_lines = []
        for i, seg in enumerate(segments):
            start_str = format_time_srt(seg['start'])
            end_str = format_time_srt(seg['end'])
            jp_text = seg['text'].strip()
            # 容错提取：如果翻译行数不够，才降级显示日文原文
            cn_text = cn_texts[i] if i < len(cn_texts) else jp_text
            
            # 💡 核心修改：去掉了末尾的 \n{jp_text}，只保留纯中文！
            srt_lines.append(f"{i + 1}\n{start_str} --> {end_str}\n{cn_text}\n")
            
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
            
        logger.info("✅ SRT 单语纯净字幕生成完毕！准备唤醒苹果 HEVC 硬件编码器...")

        # 5. 🔥 硬件压制
        quality = settings.media_engine.hardware_encode_quality
        srt_name = srt_file.name 

        cmd = [
            "ffmpeg", "-y",
            "-i", str(source_file.absolute()),
            "-vf", f"subtitles=filename={srt_name}", 
            "-c:v", "hevc_videotoolbox", 
            "-q:v", str(quality), 
            "-tag:v", "hvc1", 
            "-c:a", "copy",
            str(output_file.absolute())
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(work_dir.absolute()), 
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"🎉 [压制完成] HEVC 字幕视频已就绪！")
        else:
            logger.error(f"❌ 压制失败: {stderr.decode().strip()}")
            shutil.copy2(source_file, output_file)
            
    finally:
        if audio_file.exists(): audio_file.unlink()
        if srt_file.exists(): srt_file.unlink()

async def process_bypass(source_file: Path, output_file: Path):
    logger.info(f"⚡ [轻量直通车] AI 引擎关闭，原画质直通: {source_file.name}")
    shutil.copy2(source_file, output_file)

async def dispatch_media(source_file_path: str):
    source_file = Path(source_file_path)
    PUBLISH_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}")) / "ready_to_publish"
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    output_file = PUBLISH_DIR / f"final_{source_file.name}"
    
    if source_file.suffix.lower() in ['.mp4', '.mov'] and settings.media_engine.enable_ai_translation:
        await process_with_ai(source_file, output_file)
    else:
        await process_bypass(source_file, output_file)
    try: source_file.unlink()
    except: pass

if __name__ == "__main__":
    test_video = "/Users/tgmesmer/GloBot/GloBot_Data/iLiFE/media/ilife_official/2025556349686583620_video.mp4"
    if Path(test_video).exists():
        test_target = "test_pipeline_dummy.mp4"
        shutil.copy2(test_video, test_target)
        asyncio.run(dispatch_media(test_target))