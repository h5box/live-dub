## 项目导航
- 本机后端与处理链；原生字幕入口 asr.py，首次下载入口 setup.py。
- 包装版由 scripts/desktop.py 调度 server、ingest、processor、asr 等模块。
## 项目目录树
```text
livedub/
├── server/      HTTP、场次、服务和进程管理
├── asr.py       原生 CPU Parakeet/TitaNet；NeMo 入口在 ../svc/
├── setup.py     首次组件安装与状态
└── settings.py  用户目录、资源目录、进程命令、模型工具路径
```
## 项目架构图
- server → setup/服务管理 → ingest → processor → 字幕与配音；前端通过 HTTP/SSE 读取。
## 项目文档
- ../docs/接口.md、../docs/配置.md、../docs/原理.md。
## 项目约束
- 安装版资源走 settings.RESOURCES，可写数据走 settings.ROOT；工作进程用 python_argv。
- 识别文件路径走 asr_path；配音继续走 to_linux。
## 项目记忆
- 10-10 07:30：原生识别固定 sherpa-onnx/core 1.13.8，避免加载系统旧 onnxruntime.dll。
