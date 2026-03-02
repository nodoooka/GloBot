# 🌟 GloBot Matrix

> **专为日本地下偶像（如 iLiFE!）深度定制的 X (Twitter) ➡️ Bilibili 全自动搬运与 AI 译制引擎。**

GloBot 并非一个简单的“转发机器人”，而是一套重度绑定 macOS (Apple Silicon) 硬件算力、具备深度偶像文化理解能力（RAG）、并通过 Telegram 进行人机协同指挥（Human-in-the-loop）的极客级工业流水线。

## ✨ 核心黑科技 (Core Features)

- 🕷️ **隐身拟人爬虫**：基于 Playwright 的持久化缓存上下文，原生底层注入反风控脚本抹除 WebDriver 特征，配合生物钟（深夜自动蛰伏）与贝塞尔随机滑动，完美规避 X (Twitter) 封锁。
- 🧠 **双脑翻译 + RAG 知识库**：采用 DeepSeek (大师节点) + GLM-4-Flash (劳模节点) 双轨并行。毫秒级 X 光扫描推文，注入本地 RAG 词典，**绝不汉化成员名字**，精准还原“吧唧/生诞祭/拼盘Live”等饭圈黑话。
- ⚡ **Apple Silicon 压榨引擎**：
  - **听觉**：调用 Mac 统一内存 (`mlx-whisper`) 进行词级精准对齐的日文听译。
  - **视觉**：直接唤醒 Mac NPU (`Vision Framework`) 进行毫秒级视频大字报/花字 OCR 提取。
  - **压制**：调用 Apple 硬件编码器 (`hevc_videotoolbox`) 极速生成纯净中文字幕视频。
- 📱 **Telegram 移动指挥中枢**：全线运行状态监控。视频发布前自动挂起，将“生肉/熟肉”与截帧推送至主理人手机，在 Telegram 点击按钮即可完成最终定稿与分发。
- 🛡️ **B站高并发物理盾上传**：不依赖易失效的第三方库，全自研底层网络封包，自带 Async Retry 防抖装甲，直连 B 站图床与视频切片上传接口。

---

## 🛠️ 系统要求 (Requirements)

- **硬件**: 强烈建议运行在 **Mac Apple Silicon (M系列芯片)** 上（NPU与统一内存强依赖）。
- **环境**: Python 3.10+
- **系统依赖**: 必须在终端安装 `ffmpeg` (用于音视频切分压制) 和 `aria2` (用于多线程极速拉取媒体)。
  ```bash
  brew install ffmpeg aria2
  ```

---

## 🚀 极速部署指南 (Quick Start)

### 1. 准备环境
```bash
# 克隆仓库
git clone [https://github.com/nodoooka/GloBot.git](https://github.com/nodoooka/GloBot.git)
cd GloBot

# 创建虚拟环境并激活
python3 -m venv .venv
source .venv/bin/activate

# 安装核心依赖
pip install -r requirements.txt

# 下载 Playwright 浏览器内核
playwright install chromium
```

### 2. 配置密钥与词典
1. 复制环境模板文件：`cp .env.example .env`
2. 在 `.env` 中填入你的 Telegram Bot Token、个人 Chat ID 以及 LLM 的 API Key。
3. 检查 `config.yaml`，调整你要监控的偶像推特 ID 及作息时间。
4. 运行以下命令自动生成初始 RAG 知识库：
   ```bash
   python Bot_Media/knowledge_example_init.py
   ```

### 3. 获取双端免密通行证
GloBot 采用最安全的本地持久化扫码授权，绝对不将密码硬编码在代码中。
- **获取 Bilibili 凭证**：运行以下命令，根据终端提示扫码登录：
  ```bash
  python Bot_Publisher/bili_login.py
  ```
- **获取 X (Twitter) 凭证**：
  打开另一个终端，以调试模式启动 Chrome：
  ```bash
  /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir="/tmp/chrome_dev_test"
  ```
  然后回到主终端运行以下命令，在弹出的浏览器中登录 Twitter：
  ```bash
  python Bot_Crawler/login_auth.py
  ```

### 4. 点火起飞
```bash
python main.py
```
*启动后，请前往你的 Telegram Bot 发送 `/boot` 正式唤醒流水线！*

---

## 📱 Telegram 中枢指令
- `/boot` - 引擎点火，启动自动化流水线
- `/kill` - 强行拔除电源，彻底终止进程
- `/pause` - 关闭发布阀门，暂时挂起系统
- `/resume` - 恢复运行
- `/status` - 调取当前监控看板与今日发射数据
- `/reset <推文ID>` - 抹除单一推文的搬运记忆
- `/force <推文ID>` - 抹除记忆并打断休眠，强制抓取

---

## ⚠️ 免责声明
本项目仅供学习与自动化技术交流使用。使用本代码产生的任何平台风控、封号风险及内容版权纠纷，由使用者自行承担。请遵守各平台的使用条款（TOS）及当地法律法规。