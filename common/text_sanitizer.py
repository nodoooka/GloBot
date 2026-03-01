import re
import logging

logger = logging.getLogger("GloBot_Sanitizer")

# ==========================================
# 📖 1. 错误拼写纠正字典 (支持正则，忽略大小写)
# ==========================================
SPELL_CORRECTIONS = {
    # 纠正拼写错误：无视大小写，将 HEROINS 替换为正确的 HEROINES
    r'(?i)HEROINS': 'HEROINES',
    
    # 你可以在这里继续添加偶像常打错的词汇...
    r'(?i)iLIFE(?!\!)': 'iLiFE!',  # 自动为没加感叹号的 iLIFE 补上感叹号
}

# ==========================================
# 🚫 2. 防竞品限流字典 (规避平台流量打压)
# ==========================================
ANTI_THROTTLING = {
    r'(?i)tiktok': 'T!kTok',
    r'(?i)youtube': 'Y*uTube',
    r'(?i)instagram': 'IG',
    r'(?i)line': 'L!NE',
}

# ==========================================
# ☣️ 3. 高危 Unicode 字符清洗黑名单
# ==========================================
# \u0300-\u036F : 组合附加符号 (Zalgo 乱码元凶，如 ̫ )
# \u0600-\u06FF : 阿拉伯文 (包含 ٛ 和 ٍ 等颜文字常用变音符)
# \u0750-\u077F : 阿拉伯文补充
# \u0F00-\u0FFF : 藏文 (常用于制造堆叠字符)
# 👇 新增：日本偶像极度爱用，但必被 B 站拦截的生僻颜文字字符块
# \u1F00-\u1FFF : 希腊扩展 (包含了 ῀ )
# \u1800-\u18AF : 蒙古文 (包含了 ᢦ )
# \u0E00-\u0E7F : 泰文 (常用于当做眼睛或嘴巴)
# \u1780-\u17FF : 高棉文/柬埔寨文
DANGEROUS_UNICODE_PATTERN = r'[\u0300-\u036F\u0600-\u06FF\u0750-\u077F\u0F00-\u0FFF\u1F00-\u1FFF\u1800-\u18AF\u0E00-\u0E7F\u1780-\u17FF]+'

def sanitize_for_bilibili(text: str) -> str:
    """B 站专属终极文本净化流水线"""
    if not text:
        return ""
        
    original_text = text
    
    # 1. 执行拼写纠正
    for pattern, replacement in SPELL_CORRECTIONS.items():
        text = re.sub(pattern, replacement, text)
        
    # 2. 执行竞品词替换
    for pattern, replacement in ANTI_THROTTLING.items():
        text = re.sub(pattern, replacement, text)
        
    # 3. 物理切除高危颜文字/Unicode
    text = re.sub(DANGEROUS_UNICODE_PATTERN, '', text)
    
    if text != original_text:
        logger.debug("✨ [文本净化] 文本已成功经过净化中间件洗刷！")
        
    return text