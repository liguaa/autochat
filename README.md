# 微信 AI 聊天助手：P0 验证原型

## 文件人格接口

新 Qt 工作台支持 `personas/<人格名>/SKILL.md`。在设置中点击“重新加载人格文件”，从回复偏好选择文件人格，即可自定义表达习惯和措辞禁忌。示例：[温柔但不讨好的朋友](personas/warm-friend/SKILL.md)；完整格式、CLI 用法和打包版目录见 [文件人格说明](PERSONA_SKILLS.md)。所选文件变更后旧候选失效；动态模式仅把人格传给最终 DS 措辞，不传给 Jev 或潜台词提议阶段。

本项目从 [PLAN.md](PLAN.md) 开始实施。当前提供 NanoJev 与 Jev 中文聊天合成样例评测、Jev → DeepSeek Flash 和仅 DeepSeek 的离线回复路径。已在微信界面验证单条消息复制；尚无稳定的自动读取、新消息检测或发送能力。

## 新版对话工作台（Windows）

React 工作台、轻面板和 54px 原生悬浮入口共用 Python 会话服务。手动加入原文后点击“分析”才调用模型；可查看原文依据、修改候选并复制。复制不会发送微信消息，也不会自动记入本人消息。默认“深入分析”需要 DeepSeek 与 TypeSafe 密钥；“快速回复”只需 DeepSeek。会话原文、撤回状态和确认事实现在自动保存到 `data/sessions/`，启动时自动读取；最终分析结果另存于 `results/`，重启后需重新分析。标题旁可删除当前会话，文件移到 `data/sessions/trash/`。完整保存范围及恢复步骤见 [会话存储说明](SESSION_STORAGE.md)。

工作台和轻面板可在每条原文的“更多”菜单中点“撤回这条记录”；占位条和短暂提示均可恢复。撤回或恢复会让该会话的旧分析与候选失效，后续模型请求只使用当前有效记录；已确认事实进入待复核状态，复核前不会送入分析。操作不会撤回微信消息、清理剪贴板或删除已保存的结果文件。新版 Qt 会话保存撤回状态和原文，重启后仍可从占位条恢复；旧 Tk 入口仍只保留本次进程的会话。只想隐藏分析时仍可用“移除卡片”。

在已有 `.venv` 中安装桌面依赖并构建本地前端：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
Set-Location frontend
npm ci --cache ..\.npm-cache --registry https://registry.npmjs.org
npm run build
Set-Location ..
.\.venv\Scripts\python.exe -m autochat.qt_shell
```

可用 `--panel` 直接打开轻面板。设置中可通过原生密码输入框将 API 密钥保存到 Windows 凭据管理器；Web 界面只接收“已配置/未配置”状态。构建独立 Windows 程序运行 `powershell -ExecutionPolicy Bypass -File scripts\build_workbench.ps1`，输出位于 `dist\AutoChat\AutoChat.exe`；打包版结果与窗口位置存放在 `%LOCALAPPDATA%\AutoChat`。受限沙箱若阻止 QtWebEngine 子进程启动，可仅在该受信环境中设置 `QTWEBENGINE_DISABLE_SANDBOX=1` 后运行；常规启动保持 QtWebEngine 沙箱。新旧界面分别启动，不在同一进程混用 Tk 与 Qt。

实施范围、测试证据和未验收项目见 [FRONTEND_IMPLEMENTATION_REPORT.md](FRONTEND_IMPLEMENTATION_REPORT.md)。旧的 `autochat.bubble`、`autochat.replay_desktop` 和 CLI 入口仍可使用。

## 手动悬浮气泡

在 Windows 上启动独立 AutoChat 气泡：

```powershell
.\.venv\Scripts\python.exe -m autochat.bubble
```

气泡可拖动，左键展开，右键可重置位置或关闭；面板可收起和关闭。也可在原侧栏点“悬浮气泡”。手动选择本地会话，输入文字或点击“粘贴文字”，核对说话方后点“加入消息”；只有再点“分析”才调用模型。默认模式为“DS 提问 → Jev 判断 → DS 写措辞”，可在面板切换为现有其他模式。候选只复制到剪贴板，用户自行核对微信联系人、修改并发送；复制不会加入本人消息历史。

分析会将提交的对话发给 DeepSeek；Jev 模式还会发给 TypeSafe。处理期间输入与阶段快照暂写入本地 `results/`，任务结束后清理；最终结果仍保存在 `results/`，其中可能含聊天原文。窗口偏好仅保存位置与置顶状态到 `data/bubble_preferences.json`，不会保存聊天或密钥。气泡不读取微信窗口、不会轮询剪贴板，也不会自动填入或发送。首次使用需按下文配置相应 API 密钥。原气泡的状态测试见 [test_bubble.py](tests/test_bubble.py)；新版实现记录见 [FRONTEND_IMPLEMENTATION_REPORT.md](FRONTEND_IMPLEMENTATION_REPORT.md)。

## 脱离 Codex 运行的桌面侧栏

在 Windows 资源管理器中双击 `start_autochat.cmd`，即可启动独立窗口。先在微信中对目标消息右键“复制”，再回到侧栏点“粘贴剪贴板”；核对消息后选择“分析模式”，点“分析并生成候选”。默认“仅 DeepSeek”直接用原文分析和生成；“Jev 判断 + DeepSeek”会先向 TypeSafe 发送当前对话做结构化判断；“Jev 判断 + DeepSeek + Jev 排序”会再调用 Jev 排序同一批候选。侧栏的仅 DeepSeek 与 Jev 模式都显式关闭 DeepSeek 思考模式，便于同配置比较。NanoJev 路径留作历史研究对照。Jev 模式的五项显示百分比取自 Jev 原始输出；仅 DeepSeek 和 NanoJev 历史模式仍显示 DeepSeek 的试验性评分。候选旁的“复制”只写入剪贴板，微信发送仍由用户完成。关闭 Codex 不影响已启动的侧栏；微信与侧栏都需要在同一个已登录的 Windows 桌面会话中运行。

### 多轮回放原型

在侧栏点击“多轮回放”，可导入包含 `messages` 数组的 JSON，逐条点击“下一条并分析”；也可手动选择“对方”或“我（确认已发送）”并加入消息。当前每次回放最多 30 条消息；每条消息必须有 `role`（`me` 或 `contact`）和 `text`。示例文件为 [replay_demo.json](data/replay_demo.json)。对方消息会按已出现的前文调用所选模式，在该消息下显示字面意思、可能潜台词、需求、关系紧张程度、历史缺口、依据、模型分数和候选。更换消息或分析设置后，旧候选会标记为过期；复制候选不会成为已发送消息，只有手动加入的本人消息会进入下一轮上下文。

回放窗口支持 Jev 判断、仅 DeepSeek 对照和可选 Jev 排序。若没有可核对的历史记录，窗口会提示历史缺口；对于明确追问过去、但输入缺少依据的消息，程序会把候选改为请求核对并强制人工审核。API 或结构校验失败会显示在对应消息卡片，可重试。聊天原文与结果保存在本地 `results/`，仍由用户自行决定是否在微信中发送。

也可直接打开回放窗口：

```powershell
.\.venv\Scripts\python.exe -m autochat.replay_desktop
```

“回复语气”提供自然短句、温和关心、直接干脆、轻松随口、豆包人格（纯糊弄·纯讨好）和按我的语气。豆包人格会更热情地附和、夸赞，少讲道理；仍不编造事实或承诺，严肃风险场景会收起俏皮。按我的语气需要至少 2 条自己发过的短消息示例，用来模仿句长、用词和标点；示例随本次分析发给 DeepSeek，不会被当作当前会话事实。新增风格需重启侧栏才会显示。设计参考 [zuiti-skill](https://github.com/SilentFleetKK/zuiti-skill) 的本人语气示例、[wechat-auto-reply](https://github.com/taotao-river/wechat-auto-reply) 的身份与语气配置，以及非官方 [doubao-style](https://github.com/stellarkey/doubao-style) 的嘴甜、热情表达；没有直接安装这些项目，它们也不会自动注入当前程序。

NanoJev 路径要求本项目的 `.venv`、`.venv313`、`pytorch_env` 和 NanoJev 权重仍位于原路径。DeepSeek 密钥从 `DEEPSEEK_API_KEY` 或凭据管理器读取；Jev 模式另需 `TYPESAFE_API_KEY`。原始消息与分析文件写入本地 `results/`，该目录不纳入版本控制。可用以下命令只查看已保存结果，不发起新的 API 调用：

```powershell
.\.venv\Scripts\python.exe -m autochat.desktop --result results\wechat_scored_reply.json
```

这是微信旁的独立辅助窗口，还没有挂入微信客户端内部。普通个人微信 PC 私聊尚未找到可用的官方插件接口；第三方 UI 自动化适配器需先证明联系人识别、新消息检测和发送回执可靠，才能用于无人值守工作。当前侧栏不会自动抓取剪贴板、自动调用 API 或自动发送微信消息。

## 离线辅助回复

使用 Python 3.12 或更新版本：

```powershell
python -m autochat.cli --input data\example_conversation.json --check
```

`--check` 只校验输入和环境，不调用 API。真实调用前，推荐将自己的密钥保存到 Windows 凭据管理器；命令会在终端隐藏输入，不要把密钥写进 JSON、代码或聊天窗口：

```powershell
.\.venv\Scripts\python.exe -m pip install keyring pywin32
.\.venv\Scripts\python.exe -m autochat.credentials set
.\.venv\Scripts\python.exe -m autochat.dual --input data\example_conversation.json --output results\reply.json
```

也可使用 `DEEPSEEK_API_KEY` 环境变量。`.\.venv\Scripts\python.exe -m autochat.credentials status` 只显示是否配置，不显示密钥。

## Jev 判断与对照实验

Jev 直连固定为 `jev-1.13.0`，题目版本为 `chat-zh-v1`。从 [TypeSafe 控制台](https://console.typesafe.ai/keys)获取密钥后，可在终端隐藏输入并保存到 Windows 凭据管理器；也可只给运行评测的进程设置 `TYPESAFE_API_KEY`。不要把密钥写入 JSON 或 `results/`：

```powershell
.\.venv\Scripts\python.exe -m autochat.credentials set --provider typesafe
.\.venv\Scripts\python.exe -m autochat.credentials status --provider typesafe
```

对单条已核对的输入进行 Jev 判断、DeepSeek 生成、Jev 排序：

```powershell
.\.venv\Scripts\python.exe -m autochat.cli --mode jev-rank --thinking disabled --input data\example_conversation.json --output results\jev_reply.json
```

合成样例仅用于接口和标签冒烟，不能推断正式准确率：

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_jev --phase smoke --output results\jev_smoke.json
```

同输入 A/B/C 对照须使用经授权、脱敏的 JSON 数组，每项含 `id`、`messages`，可选 `confirmed_facts` 和 `style_profile`；格式与单条 CLI 输入相同。评测脚本为 A（仅 DeepSeek）和 B（Jev 判断后 DeepSeek）显式关闭思考模式，C 对 B 的同一批候选额外调用 Jev 排序，另生成盲评文件与分组映射。候选采纳、编辑程度和事实错误仍需人工盲评，脚本中的相对概率不能代替这些结果：

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_jev --phase compare --input data\authorized_deidentified_cases.json --output results\jev_compare.json
```

新结果记录模型版本、题目版本、token 用量、调用耗时和 `send_status: not_sent`。当前没有自动发送权限；高风险或明显歧义仍要人工审核。

若密钥只在另一个 PowerShell 的临时环境变量中，该变量不会自动传到 Codex 进程。可以在那个 PowerShell 中运行以下命令，把密钥直接移入 Windows 凭据管理器（不会打印密钥）：

```powershell
& 'D:\projects\autochat\.venv\Scripts\python.exe' -c "import os,keyring; keyring.set_password('autochat.deepseek','api-key',os.environ['DEEPSEEK_API_KEY'])"
```

可以先用 `.\.venv\Scripts\python.exe -m autochat.dual --input data\example_conversation.json --dry-run` 验证两模型路径，过程会运行 NanoJev，但不会调用 DeepSeek。输入是 JSON 对象，`messages` 为按时间顺序排列的数组，每条包含 `role`（`me` 或 `contact`）和 `text`；末条必须是 `contact`。可选 `confirmed_facts` 数组记录用户确认的事实。输出带有 `context_version` 和 `send_status: not_sent`。新消息到来后应重新生成候选，旧版本不能用于发送。

DeepSeek 使用官方 `https://api.deepseek.com/chat/completions`，模型 `deepseek-flash`。CLI 默认是仅 DeepSeek；`--mode dual` 要求同一聊天版本的 NanoJev 分析，`--mode jev` 和 `--mode jev-rank` 调用 TypeSafe。判断模型信号均标记为未经中文聊天领域验证，大模型必须结合原文复核。API 返回 JSON 后仍做结构检查；失败时本轮停止，不产生发送动作。候选和分析仍需用户审核。

侧栏各模式共用同一套回复语气和人工审核规则；切换分析模式不会自动调用 API，只有点击分析按钮才会发送当前输入给所选模式的服务。更新代码后需关闭并重新启动侧栏才能看到新的显示方式。

### 动态潜台词模式

在侧栏或多轮回放中选择“DS 提问 → Jev 判断 → DS 写措辞”，或使用 `--mode jev-dynamic`。首次 DeepSeek 只提出最多五条待判断的“可能潜台词”，附当前消息及已确认事实的逐字引用；程序会丢弃引用错误、重复或越界的条目。Jev 在一次批量请求中回答原有固定题、每条假设的原文支持度与证据充分性、五级关系紧张 Score，以及回复方向 Choice。最终潜台词和分析摘要由 Jev 答案组装。自 v2.3 起，第二次 DeepSeek **结合 Jev 回复方向、判断摘要、各条潜台词及其支持度、证据状态、原文引用和回复约束写候选短句**，只能返回 `candidates`，不能输出或改写分析、分数或审核意见。证据不足、冲突或低置信度的解释只能指导中性表达或澄清；低支持的解释不能作为回复前提。支持度未经校准，任何假设都不会升级为已确认事实。待核实信息明确标注为首次 DS 提议，引用再次按原文校验。最终措辞输入保留在结果的 `wording_brief` 中，未转发完整概率分布。Jev 失败时不让 DS 代判。程序仍做格式校验与历史事实保护。正常路径为 DeepSeek 两次、Jev 一次；默认显式关闭思考，CLI 可用 `--thinking enabled` 手动覆盖。该模式不会追加 Jev 候选排序。

```powershell
.\.venv\Scripts\python.exe -m autochat.cli --input data\dynamic_subtext_demo.json --mode jev-dynamic --thinking disabled --replay-detail --check
.\.venv\Scripts\python.exe -m autochat.cli --input data\dynamic_subtext_demo.json --mode jev-dynamic --thinking disabled --replay-detail --output results\dynamic_subtext_demo.json
```

`--check` 不调用模型。结果保留 `subtext_hypotheses`、Jev 原始题目和答案、各阶段状态/用量/耗时、`request_id`、配置指纹及 `send_status: not_sent`。未传 `--output` 时 JSON 输出到标准输出；侧栏和回放会为每次请求生成不同的文件名。桌面卡片可展开查看引用与缺失信息；Jev 完成后即显示判断，即使回复生成失败也保留分析。侧栏与回放的聊天分析卡片均可点“删除卡片”；只移除界面中的分析卡片，聊天原文和已保存的本地结果文件仍保留。候选只是待审核文本，复制不会加入历史。

支持度百分比是 Jev 对“原文是否支持该假设”的模型判断，不是聊天意图准确率或对方真实心理。Jev 判定证据不足、冲突、低置信度或歧义较高时，会标记为不确定；DeepSeek 不参与该判断。关系紧张 Score 为 0～4 的试验性描述量表，不能换算成“生气概率”。所有评分未经中文聊天领域校准。历史原话缺失时，程序会把候选改为请求核对。

同一份经授权、脱敏输入可用以下入口对照 A（仅 DeepSeek）、B（固定 Jev → DeepSeek）和 C（动态模式）；不加 `--run` 只打印待执行命令，加上才逐个调用真实 API。示例场景见 [dynamic_subtext_cases.json](data/dynamic_subtext_cases.json)。质量评价仍需人工盲评，合成冒烟不能代替真实会话验证。

```powershell
.\.venv\Scripts\python.exe -m scripts.compare_dynamic --input data\dynamic_subtext_demo.json
.\.venv\Scripts\python.exe -m scripts.compare_dynamic --input data\dynamic_subtext_demo.json --run
```

实现、模拟测试和真实接口验证记录见 [DYNAMIC_SUBTEXT_REPORT.md](DYNAMIC_SUBTEXT_REPORT.md)。

### 百分比评分测试

新结果的 `scorecard` 分开列出主要意图、情绪、是否需要回复、回复急切程度和风险。可用以下命令按截图式摘要打印：

```powershell
.\.venv\Scripts\python.exe -m autochat.scorecard --input results\wechat_scored_reply.json
```

Jev 模式中，意图与情绪的百分比是各自 Choice 题目所选类别在给定选项中的相对概率；回复需求直接显示 Noul 的“需要回复”值；急切程度显示所选急切类别的概率，并非急切强度；风险显示人身安全、钱款、隐私和重要承诺四个独立 Noul 中的最高值，界面会列出全部分项，这个最大值不是“存在任意风险”的概率。Jev 输出保存在结果文件的 `jev.answers`，DeepSeek 自报评分仍保存在 `result.scorecard`，`display_score_source` 指明界面来源。仅 DeepSeek 和 NanoJev 历史模式显示 DeepSeek 自报评分。所有这些值都未经中文聊天数据校准，不能作为自动发送阈值；风险较高、急切程度高或存在未确认假设时仍需人工审核。

## NanoJev 合成样例测试

源码固定为 `TianyuCodings/NanoJev` 的提交 `76fdfc9ecdca45a9bcef17991a07d3041a87685a`，权重固定为 `C-Tianyu/NanoJev` 的 `unified-games-v1` 快照 `047b927b30882a1138fc504821b82ac145a4b81a`。上游源码放在 `.vendor/NanoJev`，权重放在 `checkpoints/NanoJev-unified`，均不纳入项目版本控制。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install httpx huggingface_hub keyring pywin32
git -c http.sslBackend=openssl clone https://github.com/TianyuCodings/NanoJev.git .vendor\NanoJev
git -C .vendor\NanoJev checkout 76fdfc9ecdca45a9bcef17991a07d3041a87685a
.\.venv\Scripts\python.exe scripts\download_nanojev.py
```

上游原始推理入口要求 CUDA。此机的 `pytorch_env` 是 Python 3.13，已有 CUDA 版 PyTorch，但其中的 Intel OpenMP DLL 与该 PyTorch 不兼容。项目提供进程内预加载脚本，从本机 `open-webui` 环境读取兼容 DLL，不修改 Conda 环境。其余 Python 3.13 依赖安装到项目 `.venv313`：

```powershell
& 'D:\deepstudy\Miniconda\envs\pytorch_env\python.exe' -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install transformers==5.17.0 safetensors==0.8.0 huggingface_hub==1.33.0
& 'D:\deepstudy\Miniconda\envs\pytorch_env\python.exe' scripts\run_nanojev_with_pytorch_env.py --checkpoint-dir checkpoints\NanoJev-unified
& 'D:\deepstudy\Miniconda\envs\pytorch_env\python.exe' scripts\run_nanojev_with_pytorch_env.py --emotion-only --checkpoint-dir checkpoints\NanoJev-unified --mode choice
& 'D:\deepstudy\Miniconda\envs\pytorch_env\python.exe' scripts\run_nanojev_with_pytorch_env.py --emotion-only --checkpoint-dir checkpoints\NanoJev-unified --mode boolean
```

若环境路径不同，可以设置 `AUTOCHAT_LIBIOMP_DLL` 指向兼容的 `libiomp5md.dll`。`data/synthetic_cases.json` 仅有 12 条人工编写的合成样例，结果只用于接口冒烟检查，不用于宣称聊天判断质量或设置自动发送阈值。

## 验证

```powershell
python -m unittest discover -s tests -v
```

P0 进度、证据和剩余验证见 [P0_REPORT.md](P0_REPORT.md)。
