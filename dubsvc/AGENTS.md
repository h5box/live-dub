## 项目导航
- 可选 Linux GPU 配音服务；server.py 对本机处理器提供串行合成接口。
## 项目目录树
```text
dubsvc/
├── server.py          HTTP 服务、预热、超时与后端选择
├── omni_client.py     vLLM-Omni 调用与时长控制
├── engine.py          IndexTTS 进程内后端，遵循原第三方许可
├── audio.py           静音裁剪与音频处理
├── voxcpm_backend.py  VoxCPM 后端
└── omni/              固定版本补丁与 16 GB 显卡部署参数
```
## 项目架构图
- processor → server 串行队列 → omni/local/voxcpm → wav；omni 单独常驻 GPU 服务。
## 项目文档
- ../docs/接口.md：合成协议；../THIRD_PARTY.md：第三方衍生文件与模型许可。
- ../docs/dev/README.md：配音性能与方案实测。
## 项目约束
- IndexTTS 衍生代码不纳入 MIT；修改前保留其许可说明。
- 同一 GPU 不并发加载多个配音后端；原句音频通过 Linux 可读路径传入。
## 项目记忆
- 10-10 07:38：首次向导复用 Linux 安装脚本；字幕默认不启动本服务。
