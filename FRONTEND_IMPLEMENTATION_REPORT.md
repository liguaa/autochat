# AutoChat 前端实施记录

日期：2026-09-30。依据 [FRONTEND_DESIGN_PLAN.md](FRONTEND_DESIGN_PLAN.md) 实施。定位为可用桌面原型，尚未完成真实中文会话质量盲评。

## 已交付

| 阶段 | 结果 |
| --- | --- |
| F0 | 安装 PySide6 / QtWebEngine；本地 `QWebEngineView` 与 `QWebChannel` 页面加载、React 挂载和事件回传通过；实现原生 54px 透明气泡及独立窗口。 |
| F1 | React + TypeScript + Vite 工作台、轻面板、浅色设计 tokens、响应式布局和明确标注的浏览器合成示例。轻面板包含消息录入、分析、候选编辑与复制。 |
| F2 | `UIService` 统一管理会话、上下文版本、模型任务、阶段快照、取消、结果校验和候选复制；复用现有 CLI、`ReplaySession` 与 DS / Jev 流程。一次真实 `llm-only` 合成文本调用成功。 |
| F3 | 气泡拖动、位置约束、置顶菜单及状态反馈；原生密钥输入保存到现有凭据管理器；设置、错误、过期和部分成功状态。 |
| F4 | 51 项 Python 回归测试、前端构建、浏览器响应式与交互检查、PyInstaller 目录包和打包版桥接/CLI 冒烟通过。 |

新入口：`.\.venv\Scripts\python.exe -m autochat.qt_shell`。旧 CLI、Tk 气泡与回放入口保留。新 Qt 的工作台、轻面板和气泡同进程共享一个 `UIService`；旧 Tk 程序若另开进程，仍是独立会话与任务状态。

## 行为与安全边界

- 原文只由用户手动加入；“我”消息需点击“记录我已发送的文字”。复制候选不会追加消息，也不会控制微信。
- 分析按钮前检查未加入草稿。模式、回复偏好、确认事实或原文变化后，复制动作在 Python 端再次核对会话 ID、上下文版本、请求 ID、模式与候选编号。
- 动态模式按 CLI 的真实 `propose → judge → reply` 快照更新。Jev 判断失败时不由 DeepSeek 代判；写回复失败保留已完成判断。取消使晚到快照和结果失效，但已发往模型服务的请求或费用可能无法撤销。
- 前端只接收密钥配置状态。密钥输入使用 Qt 密码框，保存到 Windows 凭据管理器，不经过 WebChannel。桥接只接受列出的动作；Web 视图只允许读取打包的本地资产，外链交给系统浏览器。模型文本、原文和引用均由 React 作为文字渲染。
- 源码运行的最终结果位于项目 `results/`；打包版位于 `%LOCALAPPDATA%\AutoChat\results`。其中可能包含聊天原文。会话列表在应用关闭后不保留。

## 验证结果

| 检查 | 命令或方法 | 结果 |
| --- | --- | --- |
| Python 回归 | `python -m unittest discover -s tests -q` | 51 项通过；包含候选过期、本人消息确认、取消后的晚到快照与跨会话复制。 |
| 前端构建 | `cd frontend; npm run build` | TypeScript 检查及 Vite 构建通过，无远程字体或运行时 CDN。 |
| 真实模型闭环 | `.\.venv\Scripts\python.exe -m scripts.smoke_workbench --live` | 仅 DeepSeek 模式，合成原文返回 2 条候选，`send_status=not_sent`。未使用私人聊天内容。 |
| Qt 本地壳 | PowerShell 设置 `$env:QTWEBENGINE_DISABLE_SANDBOX='1'` 后运行 `.\.venv\Scripts\python.exe -m autochat.qt_shell --smoke --smoke-report results\qt_shell_smoke.json` | 两视图加载，React 挂载、QWebChannel 建立，追加一条合成消息后页面显示 1 条消息。 |
| 打包版 | `powershell -ExecutionPolicy Bypass -File scripts\build_workbench.ps1`，再运行 `dist\AutoChat\AutoChat.exe --smoke` | 构建成功；两视图和桥接加载，`--worker-cli --check` 退出码 0。构建包约 563 MB。 |
| 浏览器交互 | Codex 浏览器访问 Vite 开发页 | 1280×800、1440×900、1050×800、900×700、700×700、420×700、360×480 的断点及无水平溢出已检查；候选切换保留编辑、剪贴板复制、设置变更后禁用旧候选通过。`<script>` 作为原文显示，未插入脚本节点。 |
| 可复现截图 | `cd frontend; npm run screenshots` | 从本地生产构建获取合成示例截图：[工作台](design/workbench-demo.png)、[轻面板对话](design/panel-demo.png)、[轻面板回复](design/panel-reply-demo.png)。截图生成需要本机安装 Chrome 或 Edge；均带示例标识，不包含私人聊天。 |

### 启动崩溃修复（2026-09-30）

用户启动打包版时，气泡首次绘制在 `QFont.setWeight(600)` 抛出异常：Qt 的字体粗细使用 `QFont.Weight` 枚举，不能直接沿用 CSS 的数值 600。现已改为 `QFont.Weight.DemiBold`。源码及打包版 `--smoke` 均增加了气泡实际绘制检查，避免只加载 Web 视图而漏测原生气泡。重新构建的 `dist/AutoChat/AutoChat.exe` 冒烟退出码为 0，`bubble_painted=true`、两个 Web 视图加载成功；记录见 `results/packaged_qt_font_fix.json`。51 项 Python 测试仍全部通过。

受限执行环境里，QtWebEngine 默认沙箱子进程无法加载；设置 `QTWEBENGINE_DISABLE_SANDBOX=1` 后源码和打包版均加载成功。正式启动默认保持沙箱；这个回退只用于当前受限环境，不应作为常规启动设置。打包时还发现运行环境 PATH 的 Poppler ICU DLL 与 Qt6Core 导出符号不兼容，已在 [AutoChat.spec](scripts/AutoChat.spec) 排除误收集的 DLL。最终打包冒烟记录在 `results/packaged_qt_smoke.json`：1.224 秒加载两视图，进程树约 522.9 MB；该数据只代表本机受限环境中的冒烟时刻，不代表长期占用。正常启动按需创建 Web 视图。

## 仍需实机验收

1. 本环境没有 TypeSafe 密钥，因此 `jev-dynamic` 的新界面真实模型调用未运行；代码路径通过现有动态模式测试与合成快照检查。
2. 需要在普通 Windows 桌面确认 100% / 125% / 150% DPI、跨屏、中文输入法组合键、任务栏、Alt+F4 与微信旁焦点行为。当前已实现逻辑坐标工作区约束，但未把这些观察冒充实机通过。
3. 已保存浏览器对生产构建的真实渲染截图；Qt 的 `grab()` 在该环境只得到空白帧，原生窗口画面仍需实机确认。
4. 旧 Tk 与新 Qt 若分别启动为不同进程，进程内的 `analysis_gate` 不提供跨进程互斥；请勿同时对同一会话发起分析。CLI 也仍可独立运行。
5. 当前未做安装程序、签名或长期内存测试。目录包可运行，但体积较大。
