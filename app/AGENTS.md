# 界面开发

## 项目导航
- Vue 3 + TypeScript、Vite、Tauri 2；src/main.ts 入口，App.vue 组织三个页面。
- 本目录执行 npx pnpm install / dev / build；构建包含 vue-tsc 类型检查。
- 假后端 8099 配 VITE_API；具体启动命令见 README.md，无需模型或 GPU。

## 项目目录树
```text
app/
├── public/               静态资源；logo.svg 是 Logo 唯一矢量源
├── src/
│   ├── api/              类型、HTTP、SSE
│   ├── stores/           当前场次与历史状态
│   ├── components/       播放器、字幕、图表、导航等组件
│   ├── views/            观看、历史、环境三个页面
│   ├── lib/              配音混音、统计、格式与偏好
│   └── styles/           主题颜色与公共样式
├── src-tauri/            桌面外壳；icons/ 桌面图标，src/ Rust 入口
└── dev/                  mock_server.py 回放假后端
```

## 项目架构图
```text
Python HTTP/SSE → api → stores → views/components
媒体分段 → hls.js → Player；PT 事件 → 字幕与 WebAudio
public/logo.svg → 导航/favicon；Tauri icon → 桌面图标
Vite → dist → 后端托管 / Tauri 窗口
```

## 项目文档
- README.md：运行、开发、页面功能、PT 换算和 Logo 图标生成。
- ../docs/接口.md：接口数据；../docs/原理.md：时间同步设计。

## 项目约束
- 配色集中在 styles/tokens.css，light-dark() 支持系统、浅色和深色主题。
- Logo 无字体或外部资源依赖，侧栏和 favicon 共用 public/logo.svg。
- 改 Logo 时同步 src-tauri/icons 的已有桌面图标；不提交未使用的移动端图标。
- 修改完成执行 npx pnpm build；其他通用约束沿用上级 AGENTS.md。

## 项目记忆
- 10-09 20:43：Logo 使用蓝底白色气泡、声波和播放符号，统一网页与桌面标识。
