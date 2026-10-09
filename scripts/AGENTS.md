## 项目导航
- fetch.py 下载既有模型工具；build_desktop.ps1 构建 Windows 安装程序。
## 项目目录树
```text
scripts/
├── linux/             Linux/WSL 可重复安装脚本
├── build_desktop.ps1  PyInstaller 后端、Tauri NSIS；生成英语测试片
└── desktop.py         打包后端的受限模块调度入口
```
## 项目架构图
- 源码和前端产物 → PyInstaller onedir → Tauri 资源 → NSIS 安装程序。
## 项目文档
- ../docs/安装.md；../.github/workflows/windows.yml 为自动构建。
## 项目约束
- 中间文件写 .tmp，安装产物写 dist；禁止把本机设置和密钥加入安装包。
- PyInstaller 数据路径须绝对路径，避免相对 .tmp/spec 目录解释。
- 安装包只打包 configs 根目录的内置 JSON，不打包 configs/local 个人预设。
## 项目记忆
- 10-10 07:30：安装包不含模型权重；首次使用下载模型与工具。
