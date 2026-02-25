import asyncio
import os
import sys
from pathlib import Path

async def download_media(url: str, save_dir: Path, filename: str):
    """调用本机 Aria2c 进行极速多线程下载"""
    save_dir.mkdir(parents=True, exist_ok=True)
    
    safe_filename = filename.replace("?name=orig", "")
    
    cmd = [
        "aria2c",
        "--quiet=true",                   
        "--continue=true",                
        "--max-connection-per-server=16", 
        "--split=16",                     
        "--min-split-size=1M",            
        "--dir", str(save_dir),           
        "--out", safe_filename,           
        url                               
    ]
    
    print(f"⬇️ 正在极速拉取: {safe_filename} ...")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            print(f"✅ 下载成功: {safe_filename}")
            return True
        else:
            print(f"❌ 下载失败: {safe_filename}\n错误: {stderr.decode().strip()}")
            return False
            
    except Exception as e:
        print(f"❌ 调用 Aria2c 发生异常: {e}")
        return False

# ==========================================
# 本地防呆测试
# ==========================================
if __name__ == "__main__":
    # 🌟 GloBot 测试路径
    test_dir = Path(os.getenv("LOCAL_DATA_DIR", "./GloBot_Data/test_group")) / "media_test"
    test_url = "https://pbs.twimg.com/media/HB17XJwawAADZ5n.jpg?name=orig"
    
    print("🚀 启动 Aria2c 引擎单点测试...")
    asyncio.run(download_media(test_url, test_dir, "karen_test_image.jpg"))