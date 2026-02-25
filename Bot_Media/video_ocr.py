import cv2
import time
import logging
from pathlib import Path
import Foundation
import Vision
import sys
import asyncio

# 将项目根目录加入系统路径
sys.path.append(str(Path(__file__).resolve().parent.parent))
from common.config_loader import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class MacVisionOCR:
    def __init__(self):
        logger.info("⚡ 正在唤醒 Mac Apple Neural Engine (Vision Framework) ...")
        self.request = Vision.VNRecognizeTextRequest.alloc().init()
        # ⚠️ 极其关键：强制告诉 NPU 我们要抓取日语和英语！
        self.request.setRecognitionLanguages_(["ja-JP", "en-US"])
        self.request.setUsesLanguageCorrection_(True)
        # 启用高精度模式，系统会自动把任务派发给 M3 Pro 的 NPU
        self.request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)

    def extract_text_from_frame(self, frame) -> list:
        """桥接 OpenCV 与 Mac 底层 API，进行毫秒级文字提取"""
        # 将 C++ 层的 OpenCV 图像（Numpy）无损转入 Objective-C 内存池
        _, buffer = cv2.imencode('.jpg', frame)
        ns_data = Foundation.NSData.dataWithBytes_length_(buffer.tobytes(), len(buffer.tobytes()))
        
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ns_data, None)
        success, _ = handler.performRequests_error_([self.request], None)
        
        results = []
        if success:
            for observation in self.request.results():
                text = observation.topCandidates_(1)[0].string()
                bbox = observation.boundingBox()
                # 转换 Vision 的左下角坐标系为 [x_min, y_min, x_max, y_max] 比例坐标
                x_min, y_min = bbox.origin.x, bbox.origin.y
                x_max, y_max = x_min + bbox.size.width, y_min + bbox.size.height
                
                results.append({
                    "text": text,
                    "box": [x_min, y_min, x_max, y_max],
                    "height": bbox.size.height
                })
        return results

def calculate_iou(box1, box2):
    """计算交并比 (IOU) - 用于判断是不是同一句花字一直停在屏幕上"""
    x_left = max(box1[0], box2[0])
    y_top = max(box1[1], box2[1])
    x_right = min(box1[2], box2[2])
    y_bottom = min(box1[3], box2[3])

    if x_right < x_left or y_bottom < y_top:
        return 0.0

    intersection = (x_right - x_left) * (y_bottom - y_top)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    return intersection / (area1 + area2 - intersection)

# ==========================================
# 🚀 视频花字时空提取主轴
# ==========================================
async def extract_video_text(video_path: Path) -> list:
    logger.info(f"👁️ [视觉引擎启动] 开始扫描视频大字报: {video_path.name}")
    start_time = time.time()
    
    ocr_engine = MacVisionOCR()
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0

    # 每秒抽 4 帧 (足够捕捉地下偶像的快闪字幕)
    frame_interval = int(fps / 4)
    
    min_height = settings.media_engine.ocr_min_height_ratio
    iou_thresh = settings.media_engine.ocr_iou_threshold

    active_texts = []
    final_texts = []
    
    frame_count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        if frame_count % frame_interval == 0:
            current_sec = frame_count / fps
            raw_results = ocr_engine.extract_text_from_frame(frame)
            
            # 1. 过滤：丢掉高度小于 3% 的背景杂字（比如衣服上的小logo）
            valid_results = [r for r in raw_results if r['height'] >= min_height]
            
            # 2. 时空融合：检查当前字幕是不是上一秒就在屏幕上了
            new_active_texts = []
            for current_item in valid_results:
                matched = False
                for active_item in active_texts:
                    # 只要位置高度重合 (IOU > 0.8) 或者文字完全一样，我们就认为是同一句台词！
                    if calculate_iou(current_item['box'], active_item['box']) > iou_thresh or current_item['text'] == active_item['text']:
                        active_item['end_time'] = current_sec # 延长存活时间
                        active_item['box'] = current_item['box'] # 更新最新位置
                        new_active_texts.append(active_item)
                        matched = True
                        break
                
                # 这是一个全新的花字！
                if not matched:
                    new_active_texts.append({
                        "text": current_item['text'],
                        "start_time": current_sec,
                        "end_time": current_sec + 0.5, # 至少给 0.5 秒的存活期
                        "box": current_item['box']
                    })
            
            # 3. 把已经消失的花字结算归档
            for active_item in active_texts:
                if active_item not in new_active_texts:
                    final_texts.append(active_item)
                    
            active_texts = new_active_texts
            
        frame_count += 1

    cap.release()
    # 结算最后一波还没消失的字幕
    final_texts.extend(active_texts)
    
    cost_time = time.time() - start_time
    logger.info(f"✅ [视觉扫描完毕] 耗时 {cost_time:.2f} 秒！共捕获 {len(final_texts)} 句硬字幕。")
    
    # 按时间轴排序返回
    return sorted(final_texts, key=lambda x: x['start_time'])

# ==========================================
# 🧪 本地单点测试
# ==========================================
if __name__ == "__main__":
    # ⚠️ 请把这里替换为你电脑上一段有“大号日文字幕”的短视频绝对路径！
    test_video = Path("/Users/tgmesmer/GloBot/GloBot_Data/iLiFE/media/ilife_official/2025556349686583620_video.mp4")
    
    async def run_test():
        if not test_video.exists():
            print("❌ 找不到视频，请替换最底部的 test_video 路径！")
            return
            
        results = await extract_video_text(test_video)
        
        print("\n" + "="*50)
        print("🎯 NPU 提取到的屏幕花字时间轴：")
        print("="*50)
        for item in results:
            print(f"[{item['start_time']:05.2f}s -> {item['end_time']:05.2f}s] {item['text']}")
            
    asyncio.run(run_test())