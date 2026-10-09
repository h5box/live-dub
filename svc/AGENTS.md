## 项目导航
- NeMo 识别服务，运行在 Linux/WSL CPU；Windows 默认原生 ONNX 入口见 ../livedub/。
## 项目目录树
```text
svc/
└── asr_service.py  Parakeet 词级识别、热词增强和 TitaNet 声纹接口
```
## 项目架构图
- 本机 processor → HTTP → NeMo CPU 模型；请求 wav 路径须为 Linux 可读路径。
## 项目文档
- ../docs/安装.md：环境安装；../docs/接口.md：识别协议。
## 项目约束
- 依赖装在独立 Linux 环境，由 ../scripts/linux/setup_asr.sh 管理。
## 项目记忆
- 10-10 07:36：桌面安装包包含服务源码，NeMo 环境和权重单独安装。
