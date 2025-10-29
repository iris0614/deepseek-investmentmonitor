## DeepSeek Positions Monitor

实时监控 DeepSeek Chat V3.1 在 Alpha Arena 页面上的 ACTIVE POSITIONS（持仓）变化，并在变化时提醒你。

### 它能做什么
- 自动打开浏览器访问 `https://nof1.ai/models/deepseek-chat-v3.1`
- 每 10 秒检查一次 ACTIVE POSITIONS 文本是否变化
- 有变化时：
  - 终端打印提示（含 Unrealized P&L 变化摘要，如果页面上能识别）
  - 发送桌面通知（系统通知）
  - 把最新持仓内容写入日志 `positions-log.txt`
  - 保存截图到 `positions_snapshots/positions_<时间>.png`

### 安装步骤
1) 安装 Python（建议 3.9+）。
2) 打开终端，进入项目目录后运行基础依赖：
   - `pip install playwright plyer`
   - `playwright install`
3) （可选）如需可视化表格，安装 rich：
   - `pip install rich`
4) （可选）在 Linux 上如需弹窗功能，可能需要安装 tkinter：
   - `sudo apt-get install python3-tk` （Ubuntu/Debian）
5) 运行脚本：
   - `python monitor_deepseek_positions.py` （默认桌面通知）
   - 或使用其他提醒方式组合（见下方“提醒方式选择”）

### 使用说明
- 启动后会自动打开一个浏览器窗口并加载 DeepSeek 模型页面。
- 没有变化时，终端会打印心跳“no change”。
- 当持仓发生变化时：
  - 终端会打印一行 “⚡ Positions updated!” 提示；
  - 如果能识别到 Unrealized P&L 数值，还会显示变化量（Δ）；
  - 系统会弹出桌面通知；
  - 会把最新内容以一行 JSON 追加到日志文件；
  - 同时保存一张截图以便回看。

### 可视化模式（终端彩色表格）
- **可选安装**：`pip install rich`
- **运行**：`python monitor_deepseek_positions.py --visual`
- **功能**：有变化时，在终端以彩色表格展示每个仓位：
  - 币种（Symbol）
  - 方向（Side: Long/Short）
  - 杠杆（Leverage: 10X）
  - 入场价格（Entry Price）
  - Unrealized P&L（**正值为绿色**，**负值为红色**）
  - **总盈亏汇总**（显示所有仓位合计）

### 提醒方式选择
你可以选择一种或多种提醒方式，它们可以组合使用：

- **桌面通知** (`--notify`)
  - 需要安装：`pip install plyer`
  - 运行示例：`python monitor_deepseek_positions.py --notify`
  - 系统会弹出桌面通知（macOS/Windows/Linux 通用）

- **声音提醒** (`--sound`)
  - 无需额外安装，使用系统默认声音
  - 运行示例：`python monitor_deepseek_positions.py --sound`
  - macOS 播放 Glass 音效，Linux/Windows 使用系统提示音

- **弹窗详情** (`--popup`)
  - 通常 Python 自带 tkinter（macOS/Linux 可能需安装：`sudo apt-get install python3-tk`）
  - 运行示例：`python monitor_deepseek_positions.py --popup`
  - 弹出一个窗口，显示持仓详情（币种、方向、杠杆、盈亏等），需手动关闭

**组合使用示例**：
```bash
# 可视化表格 + 桌面通知 + 声音
python monitor_deepseek_positions.py --visual --notify --sound

# 弹窗详情 + 声音
python monitor_deepseek_positions.py --popup --sound

# 仅桌面通知（默认，无需参数）
python monitor_deepseek_positions.py --notify
```

**默认行为**：如果未指定任何提醒参数，程序默认使用桌面通知 (`--notify`)。

### 输出文件在哪里
所有文件都会保存在脚本所在目录：
- **日志文件**：`positions-log.txt` - 每次变化都会追加一行 JSON 记录
- **截图文件夹**：`positions_snapshots/` - 每次变化保存一张截图（文件名：`positions_YYYYMMDD_HHMMSS.png`）
- **HTML 预览**：`positions_latest.html` - 最新的持仓表格（可直接用浏览器打开）

### 如何停止程序
- 在终端按下 `Ctrl + C` 即可安全退出。

### 常见问题（FAQ）
- 通知不显示怎么办？
  - 请先确认已安装 `plyer`：`pip install plyer`
  - 确认系统“通知”权限允许终端/命令行工具弹出通知
  - 某些平台可能需要把通知中心打开到“允许”状态
  - 如果桌面通知不可用，可以尝试使用 `--sound` 或 `--popup` 方式
- 声音提醒不响？
  - macOS/Linux/Windows 会自动选择系统默认声音
  - 如果系统声音被静音，提醒音也会静音
- 弹窗不显示？
  - 确认系统已安装 tkinter（macOS/Linux 可能需要单独安装：`sudo apt-get install python3-tk`）
  - 某些无图形界面的环境无法使用弹窗
- 刷新频率可以改吗？
  - 可以。在 `monitor_deepseek_positions.py` 中搜索 `await asyncio.sleep(10)`，把 10 改为你想要的秒数即可。
- 页面加载失败怎么办？
  - 脚本会自动等待 30 秒后重试；若网络不稳定，可稍等片刻或检查网络。
- 文件路径可以改吗？
  - 所有文件默认保存在脚本所在目录（相对路径）。
  - 如需修改，编辑脚本中的 `SCRIPT_DIR`、`LOG_PATH`、`SNAP_DIR` 常量即可。
  - `positions_latest.html` 会在同目录生成，可直接用浏览器打开查看。
- 启动时显示什么信息？
  - 程序启动时会显示：目标 URL、日志路径、截图路径、已启用的功能（可视化模式、提醒方式）和依赖状态。

### 安全说明
- 本工具只会读取公开网页内容，不会进行任何交易或账户操作。
- 不会保存你的任何敏感信息；日志中仅包含页面提取的公开文本。

### 运行预期示例
```json
{"timestamp": "2025-10-29T19:08:44Z", "model": "DEEPSEEK CHAT V3.1", "active_positions": "ETH short 2.4, SOL long 1.2, ..."}
```




