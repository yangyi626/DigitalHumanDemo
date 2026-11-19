# 第三方代码与素材来源

- 数字人运行时来自 [kleinlee/DH_live](https://github.com/kleinlee/DH_live)。本项目集成了 `avatar/js/DHLiveMini.js`、`avatar/DHLiveMini.wasm` 及配套网页渲染代码；在渲染脚本中增加了音频播放时钟、生命周期和加载状态接口。上游 README 声明 MIT，原网页脚本中的版权标记保持不变。
- `avatar/js/pako.min.js` 为 pako 解压库，原文件中的 MIT / Zlib 许可标记保持不变。WASM 内嵌依赖的许可标记保持不变。
- `avatar/assets/`、`avatar/assets2/`、`avatar/background/` 和 `avatar/common/` 是随原 DH_live 网页资源取得的配套素材。未移除素材水印。
- `avatar/assets-higgs/` 为专家参考形象生成的待机视频和 DH_live 预处理参数，沿用本项目已有部署素材。人物准备由 Higgs Avatar 与 DH_live 工具完成；运行时无需这些准备服务。用户此前授权该人物用于本项目。该记录不是对人物肖像、商标或第三方素材的额外授权，具体使用范围沿用素材来源条件。
- `web/` 页面基于原专家咨询 Demo 改写。默认人物与默认语音服务音色分别配置，使用者可设置自己账户中可用的 `BOSON_VOICE`。
- DeepSeek 和 Boson/Higgs 通过用户自己的服务端 Key 调用；其服务和模型遵循各自条款。

本仓库不包含真实 API Key、原部署站点配置备份或一次性人物制作环境。无需另行克隆或启动 DH_live 原仓库。
