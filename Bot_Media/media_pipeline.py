import os
import shutil
import asyncio
import logging
from pathlib import Path
import sys
import re
import time

sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings
from Bot_Media.audio_transcriber import extract_audio, transcribe_audio
from Bot_Media.video_ocr import extract_video_text
from Bot_Media.llm_translator import translate_batch 

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", f"./GloBot_Data/{settings.targets.group_name}"))

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
        ocr_task = asyncio.create_task(extract_video_text(source_file))
        audio_task = asyncio.create_task(extract_audio(source_file, audio_file))
        ocr_results, audio_success = await asyncio.gather(ocr_task, audio_task)
        
        if not audio_success: return

        whisper_results = await transcribe_audio(audio_file)
        segments = whisper_results.get('segments', [])
        if not segments:
            shutil.copy2(source_file, output_file)
            return

        logger.info(f"🧬 开始双模态上下文融合，打包发送给 AI 翻译中...")
        cn_texts = await translate_batch(segments, ocr_results)
        
        srt_lines = []
        for i, seg in enumerate(segments):
            start_str = format_time_srt(seg['start'])
            end_str = format_time_srt(seg['end'])
            jp_text = seg['text'].strip()
            cn_text = cn_texts[i] if i < len(cn_texts) else jp_text
            srt_lines.append(f"{i + 1}\n{start_str} --> {end_str}\n{cn_text}\n")
            
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
            
        logger.info("✅ SRT 单语纯净字幕生成完毕！准备唤醒苹果 HEVC 硬件编码器...")

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
    PUBLISH_DIR = DATA_DIR / "ready_to_publish"
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    output_file = PUBLISH_DIR / f"final_{source_file.name}"
    
    if source_file.suffix.lower() in ['.mp4', '.mov'] and settings.media_engine.enable_ai_translation:
        await process_with_ai(source_file, output_file)
    else:
        await process_bypass(source_file, output_file)
    try: source_file.unlink()
    except: pass

# ==========================================
# 🧹 媒体综合管理暴露接口
# ==========================================
def cleanup_old_media(retention_days=2.0):
    media_dir = DATA_DIR / "media"
    if not media_dir.exists(): return
    current_time = time.time()
    cutoff_time = current_time - (retention_days * 24 * 3600)
    deleted_files = 0
    for file_path in media_dir.rglob('*'):
        if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
            try:
                file_path.unlink()
                deleted_files += 1
            except Exception: pass
    for member_dir in media_dir.iterdir():
        if member_dir.is_dir() and not any(member_dir.iterdir()):
            try: member_dir.rmdir()
            except: pass
    if deleted_files > 0:
        logger.info(f"🧹 [空间管理] 触发自动清理！已永久销毁 {deleted_files} 个陈旧媒体文件。")

def cleanup_media(media_paths):
    for f in media_paths:
        if "ready_to_publish" in str(f):
            try: Path(f).unlink()
            except: pass

async def process_media_files(media_list):
    final_paths = []
    video_info = {"original": None, "translated": None}
    
    for mf in media_list:
        if str(mf).lower().endswith(('.mp4', '.mov')):
            logger.info(f"   -> 正在启动媒体管线压制视频...")
            source_file = Path(mf)
            PUBLISH_DIR = DATA_DIR / "ready_to_publish"
            PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
            
            orig_file = PUBLISH_DIR / f"orig_{source_file.name}"
            shutil.copy2(source_file, orig_file)
            video_info["original"] = str(orig_file)
            final_paths.append(str(orig_file))
            
            output_file = PUBLISH_DIR / f"final_{source_file.name}"
            await dispatch_media(str(source_file))
            
            if output_file.exists():
                if getattr(settings.media_engine, 'enable_ai_translation', False):
                    video_info["translated"] = str(output_file)
                final_paths.append(str(output_file))
        else:
            final_paths.append(mf)
            
    return final_paths, video_info

if __name__ == "__main__":
    test_video = "/Users/tgmesmer/Downloads/9QXPkq3RAjeUb0JW.mp4"
    if Path(test_video).exists():
        test_target = "uth_pipeline_dummy.mp4"
        shutil.copy2(test_video, test_target)
        asyncio.run(dispatch_media(test_target))