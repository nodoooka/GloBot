import os
import yaml
import sys
from pydantic import BaseModel, Field, model_validator
from dotenv import load_dotenv
from pathlib import Path

# 加载 .env 文件中的隐私密钥
load_dotenv()

# ==========================================
# Pydantic 数据模型：强类型校验防线
# ==========================================
class AppInfo(BaseModel):
    name: str
    version: str
    description: str

class TargetConfig(BaseModel):
    group_name: str
    x_accounts: list[str]
    account_title_map: dict[str, str] = Field(default_factory=dict)

class CrawlerGlobalSettings(BaseModel):
    max_retries: int = Field(default=3, ge=1, le=5) 
    scroll_timeout_ms: int
    scroll_depth: int

class CrawlerPlatformConfig(BaseModel):
    enable: bool = False
    fetch_text: bool = True
    fetch_images: bool = True
    fetch_videos: bool = True

class CrawlersConfig(BaseModel):
    global_settings: CrawlerGlobalSettings
    x_twitter: CrawlerPlatformConfig
    tiktok: CrawlerPlatformConfig
    instagram: CrawlerPlatformConfig
    youtube: CrawlerPlatformConfig

class MediaEngineConfig(BaseModel):
    enable_ai_translation: bool = False
    whisper_model: str
    ocr_iou_threshold: float
    ocr_min_height_ratio: float
    hardware_encode_quality: int

class SystemConfig(BaseModel):
    max_ram_percent: float
    max_temp_celsius: float
    # 👈 新增：注册媒体保留天数，并限制最小值不能低于 0.5 天 (12小时)
    media_retention_days: float = Field(default=2.0, ge=0.5, description="媒体文件最大保留天数")

# 👇 1. 新增：视频分区与标签的预设模型
class VideoPresetConfig(BaseModel):
    name: str
    tid: int
    tags: str

class BilibiliPublisherConfig(BaseModel):
    visibility: int = Field(default=1, description="0为公开, 1为仅自己可见")
    title: str = Field(default="", max_length=20)
    allow_comment: bool = True
    creation_declare: int = Field(default=2, description="1为原创, 2为转载")
    schedule_time: str = ""
    publish_text_image: bool = True
    publish_original_video: bool = False
    publish_translated_video: bool = False
    video_copyright: int = 2
    video_tid: int = 171
    video_tags: str = "iLiFE!,地下偶像"
    
    # 👇 2. 新增：注入预设选项列表。默认写死了几套配置防止 yaml 没更新时报错
    video_presets: list[VideoPresetConfig] = Field(default_factory=lambda: [
        VideoPresetConfig(name="地下偶像区 (171)", tid=171, tags="美少女,地偶,日本地偶,地下偶像,日本偶像,日本地下偶像,iLiFE!,地下偶像,Globot"),
        VideoPresetConfig(name="宅舞区 (20)", tid=20, tags="舞蹈,宅舞,美少女,地下偶像,iLiFE!"),
        VideoPresetConfig(name="日常 Vlog (174)", tid=174, tags="日常,vlog,美少女,地下偶像,iLiFE!")
    ])

class PublishersConfig(BaseModel):
    bilibili: BilibiliPublisherConfig

# 👇 新增：提示词配置数据模型
class PromptsConfig(BaseModel):
    tweet_translation_prompt: str
    video_translation_prompt: str

# 👑 全局顶层模型
class AppConfig(BaseModel):
    app: AppInfo
    targets: TargetConfig
    crawlers: CrawlersConfig
    media_engine: MediaEngineConfig
    publishers: PublishersConfig
    system: SystemConfig
    prompts: PromptsConfig  # 👈 新增：将提示词引擎接入全局配置

    # ==========================================
    # 🚨 核心黑科技：跨模块冲突拦截器 (防呆设计)
    # ==========================================
    @model_validator(mode='after')
    def validate_cross_dependencies(self) -> 'AppConfig':
        bili = self.publishers.bilibili
        media = self.media_engine
        x_spider = self.crawlers.x_twitter

        errors = []

        if bili.publish_translated_video and not media.enable_ai_translation:
            errors.append(
                "❌ 【配置冲突】发布端开启了 [publish_translated_video] (发送翻译视频)，\n"
                "   但媒体引擎的 [enable_ai_translation] (AI翻译总闸) 是关闭的！\n"
                "   👉 解决办法：请将 media_engine.enable_ai_translation 改为 true。"
            )

        if (bili.publish_original_video or bili.publish_translated_video) and not x_spider.fetch_videos:
            errors.append(
                "❌ 【配置冲突】发布端要求发送视频，\n"
                "   但推特爬虫的 [fetch_videos] 是关闭的！\n"
                "   👉 解决办法：请将 crawlers.x_twitter.fetch_videos 改为 true。"
            )

        if bili.publish_text_image and not (x_spider.fetch_text or x_spider.fetch_images):
            errors.append(
                "❌ 【配置冲突】发布端开启了 [publish_text_image] (图文动态)，\n"
                "   但爬虫的文本和图片抓取全关了！\n"
                "   👉 解决办法：请至少开启 fetch_text 或 fetch_images。"
            )

        if bili.publish_original_video and bili.publish_translated_video:
            print("⚠️ 【警告】您同时开启了发送原版视频和翻译视频，这可能会导致向B站上传两份高度相似的稿件。")

        if errors:
            print("\n" + "="*60)
            print("🚨 致命配置错误拦截 🚨")
            print("="*60)
            for err in errors:
                print(err)
            print("="*60 + "\n")
            sys.exit(1)

        return self

# ==========================================
# 核心加载逻辑
# ==========================================
def load_config() -> AppConfig:
    base_dir = Path(__file__).resolve().parent.parent
    yaml_path = base_dir / "config.yaml"
    
    if not yaml_path.exists():
        print(f"❌ 找不到配置文件: {yaml_path}")
        sys.exit(1)
        
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        
    try:
        return AppConfig(**data)
    except Exception as e:
        print(f"\n❌ config.yaml 格式错误或类型不匹配:\n{e}\n")
        sys.exit(1)

settings = load_config()

# 环境变量重载与 PII 隔离
MASTER_LLM_API_KEY = os.getenv("MASTER_LLM_API_KEY")
WORKER_GLM_API_KEY = os.getenv("WORKER_GLM_API_KEY")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
BILI_ACCOUNT_NAME = os.getenv("BILI_ACCOUNT_NAME", "GloBot搬运")  # 🛡️ 账号名转移至此！

if __name__ == "__main__":
    print(f"✅ 核心配置文件加载成功！当前目标团体: {settings.targets.group_name}")
    print(f"✅ B站可见范围设定: {'仅自己可见' if settings.publishers.bilibili.visibility == 1 else '公开'}")
    print(f"✅ 监控账号加载完毕: 共 {len(settings.targets.x_accounts)} 个。")