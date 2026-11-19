# DigitalHumanDemo · 数字人专家咨询

支持文字问答、语音问答、实时视频和实时通话。DeepSeek 生成回答，Boson/Higgs 提供语音识别与合成，DH_live 在浏览器中渲染数字人。

仓库已包含人物视频、配套参数和 WASM 运行文件，无需启动 DH_live 原仓库、安装 PyTorch 或配置 GPU。也不需要 Docker。

## 快速开始

准备 **Python 3.11 或 3.12**、Git，以及可用的 Boson/Higgs 和 DeepSeek API Key。当前代码使用 `asyncio.timeout` 和 `audioop`，不支持 Python 3.10 或 3.13 及以上版本。首次安装依赖需要联网。

```bash
git clone https://github.com/yangyi626/DigitalHumanDemo.git
cd DigitalHumanDemo
```

复制 `.env.example` 为 `.env`，只需填写两个必填项：

```dotenv
BOSON_API_KEY=你的_Boson_Higgs_Key
DEEPSEEK_API_KEY=你的_DeepSeek_Key
```

然后运行：

```bash
python run.py
```

Linux / macOS 可使用 `python3.11 run.py`，Windows 可使用 `py -3.11 run.py`。脚本会自动建立 `.venv`、安装锁定的运行依赖并启动服务。如果没有 `.env`，首次运行会自动创建样例并提示填写 Key。

打开 **http://localhost:8789/**。无需发布网页快照，也无需安装 Node.js。`BOSON_VOICE=default` 使用语音服务默认音色；专属专家音色需要在自己的 Boson 账户中配置可用的 Voice ID。

Key 需要对应服务的模型权限和可用额度。默认模型为 `deepseek-flash`、`higgs-tts-3`、`higgs-realtime` / `higgs-stt-3.1`。数字人画面在用户设备上运行；语音和问答会调用云服务。

## 四种互动方式

| 模式 | 输入 | 输出 | 插话 |
| --- | --- | --- | --- |
| 文字问答 | 打字 | 字幕、语音 | 播完再问 |
| 语音问答 | 麦克风 | 字幕、语音 | 播完再问 |
| 实时视频 | 麦克风 | 数字人、字幕、语音 | 支持 |
| 实时通话 | 麦克风 | 字幕、语音 | 支持 |

本机 `localhost` 可以使用麦克风。手机或其他设备访问服务器时，需要配置可信 HTTPS；普通 HTTP 的服务器 IP 地址无法正常使用浏览器麦克风。实时视频还需要浏览器支持 WebGL2 和 WASM。

## 配置

`.env.example` 中除两个 Key 外的选项均可保持默认：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `BOSON_VOICE` | `default` | 语音合成音色 |
| `HOST` | `127.0.0.1` | 服务监听地址 |
| `PORT` | `8789` | 服务端口 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 问答模型 |
| `VOICE_ASR_PROVIDER` | `higgs` | 语音问答识别提供方；默认覆盖四种模式 |
| `SESSION_SECONDS` | `300` | 单次会话最长秒数 |
| `MAX_SESSIONS` | `16` | 会话数量保护上限，并非已验证的持续并发容量 |
| `SESSION_SIGNING_KEY` | 留空 | 启动自动生成；配置固定随机值可让访客标识跨重启保持有效 |

使用 `python run.py --check` 检查依赖和配置。此检查不调用云服务，也不验证 Key 的有效性。

若希望手动管理环境：

```bash
python -m venv .venv
# Linux / macOS
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python server.py
# Windows 对应使用 .venv\Scripts\python.exe
```

## 项目结构

```text
run.py                 自动安装依赖、检查配置与启动
server.py              HTTP、WebSocket、云端问答和语音调用
barge_in.py            实时模式插话确认
voice_turn.py          可选语音识别分支的整句音频收集
web/                   页面、录音、字幕和语音播放器
avatar/                DH_live 渲染代码、WASM、人物视频和参数
deploy/                Linux 服务、Nginx 和网页版本发布
tests/                 使用模拟云服务的会话测试
docs/                  架构、部署与验证说明
.env.example           不含密钥的配置样例
requirements-lock.txt  锁定的运行依赖
THIRD_PARTY.md         第三方代码和人物素材来源
```

## 测试与部署

安装运行依赖后可执行测试，不需要真实 Key，也不会调用付费服务：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Linux 服务器、HTTPS 代理及可选的网页版本发布见 [部署说明](docs/deployment.md)。实现流程见 [架构说明](docs/architecture.md)，本次验证见 [验证记录](docs/validation.md)。

`.env`、虚拟环境、构建缓存、日志和网页发布快照均由 `.gitignore` 排除。运行资源已经包含在仓库中，迁移时重新安装依赖并填写自己的 Key 即可。

第三方代码和素材来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。
