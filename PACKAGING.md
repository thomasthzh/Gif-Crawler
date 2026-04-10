# GIF 爬虫打包说明（macOS）

## 1) 安装打包工具
```bash
python3 -m pip install --user pyinstaller
```

## 2) 一键打包
```bash
./build.command
```

产物：
- `dist/gif-crawler`（可执行文件）

## 2.1) 打包 GUI 桌面版
```bash
./build-gui.command
```

产物：
- `dist/GIF-Crawler.app`（双击运行）
- GUI 支持中英双语界面，并带实时任务反馈（进度条、成功/失败/GIF 计数、日志）
- GUI 支持停止任务（Stop）和本地任务历史（SQLite，默认 `task-history.sqlite3`）

若双击 `.app` 无法启动，可用：
```bash
./open-gui.command
```
这个脚本会先清理隔离标记再打开 App。

## 2.2) Windows/Linux 自动打包（CI）
已提供 GitHub Actions：
- `.github/workflows/build-cross-platform.yml`

触发方式：
1. 推送到 `main/master`，或手动触发 `workflow_dispatch`
2. 在 Actions 下载构建产物：
- `GIF-Crawler-Windows.exe`
- `GIF-Crawler-Linux`

说明：
- 本机（macOS）通常不能直接原生交叉产出 Windows/Linux 可执行文件；
- 该工作流会在对应系统 Runner 上原生打包，产物可直接使用。

## 3) 运行示例
```bash
./dist/gif-crawler --url https://example.com --output ./scrape-report.html
```

## 4) 发布建议（下一步）
- 增加 GUI（桌面应用）而非纯命令行。
- 加入任务队列、断点续传、失败重试和限速策略。
- 增加站点规则配置（反爬策略、选择器模板、Cookie 会话管理）。
- 做版本化配置目录与日志归档。
- 引入自动化测试与 CI，固定发布流程。
