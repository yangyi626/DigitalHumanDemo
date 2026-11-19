# 专家音色

`reference.wav` 为本项目已有专家参考录音，`reference.txt` 是对应逐字稿。音频为 24 kHz 单声道 WAV，约 3.25 秒，沿用原专家音色的同一句首页问候素材。

默认 `.env` 设置：

```dotenv
BOSON_VOICE=expert
```

服务启动时：

1. 使用用户填写的 Boson Key 检查原线上专家音色 ID 是否在其账户中可用。
2. 可用时直接使用原音色；不可用时，把本目录的参考录音和逐字稿上传到用户自己的 Boson 账户，注册可复用音色。
3. 将取得的 Voice ID 用于文字问答、语音问答、实时视频和实时通话的全部播报。

因此，新用户只需填写两个 Key，不必手动创建音色或找原部署账户的专属 ID。重复上传相同录音由 Boson 返回该账户已有的音色，不重复占用名额。音色初始化失败时启动会报错，便于排查凭据、网络或音色数量限制。

如已有可用音色，可以把 `BOSON_VOICE` 直接设为自己的 `voice_...`；设为 `default` 时使用 Boson 通用音色。`run.py --check` 仍只检查本地依赖和 Key 是否填写，不调用音色服务；正式启动时才准备音色。

原账户复用的是原音色 ID。其他账户根据参考录音重新克隆，音色效果由 Higgs 模型决定，不能保证与原音色逐样本一致。参考录音来自本项目既有专家素材，来源说明见 [THIRD_PARTY.md](../../THIRD_PARTY.md)。

接口依据：[Boson 自定义音色说明](https://docs.boson.ai/custom-voices-and-avatars)、[创建音色](https://docs.boson.ai/api-reference/audio/create-a-voice)。
