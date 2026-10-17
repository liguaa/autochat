# 动态潜台词模式执行报告

日期：2026-09-29。任务依据：[SOL_DYNAMIC_SUBTEXT_TASK.md](SOL_DYNAMIC_SUBTEXT_TASK.md)。

## 已实现

- `jev-dynamic`：DeepSeek 提议最多五条待判断的可能潜台词，程序逐来源核验引用并去重；Jev 一次批量判断固定题、每条假设的 Noul 支持度与 Choice 证据状态、五级关系紧张 Score 和回复方向 Choice。结果中的潜台词、分析摘要与评分由 Jev 答案组装。第二次 DeepSeek 只按照 Jev 选定的回复方向写候选短句，响应只能含 `candidates`，不能输出潜台词判断或审核意见，也不接收 Jev 概率分布。正常路径为 DS 2 次、Jev 1 次。
- 严格核验 Jev Noul、Choice、Score 的题型、键集合、范围、有限数值、概率和、Score 分布加权值及量表说明。支持度保留接口原值；证据不足、冲突或 Jev 低置信度会标记为不确定。DS 提出的缺失信息只供查看，不覆盖 Jev 结论。
- 假设失败最多修复一次后改走固定 Jev；Jev 失败保留未判断假设且不生成候选，不由 DS 代判；DS 措辞失败保留 Jev 分析与阶段错误，候选为空。阶段记录模型、版本、题目与哈希、耗时、usage、请求次数、降级原因、请求 ID 和配置指纹，`send_status` 恒为 `not_sent`。
- CLI、桌面侧栏和多轮回放加入新模式。Tk 界面在后台执行网络请求，从阶段快照先显示 Jev 判断，再显示候选；卡片可展开原文引用和缺失信息。聊天分析卡片有明确标题并可从侧栏或回放删除；删除仅移除界面卡片，保留聊天原文及已保存的本地结果文件。桌面与回放每次请求使用独立文件名；回放以请求令牌阻止同上下文重试的晚到结果覆盖新结果。
- 历史防护覆盖“先问昨天说过什么，下一句只说那你说”；可见历史中有明确依据时不强制请求提醒。本人风格样本只进入最终回复提示，不进入 Jev 判断材料。

主要文件：`autochat/hypotheses.py`、`autochat/dynamic.py`、`autochat/jev_reply.py`、`autochat/dynamic_ui.py`、`autochat/jev.py`、`autochat/cli.py`、`autochat/desktop.py`、`autochat/replay.py`、`autochat/replay_desktop.py`、`autochat/scorecard.py`、`scripts/compare_dynamic.py`、`tests/test_dynamic.py`、`data/dynamic_subtext_demo.json`、`data/dynamic_subtext_cases.json`、`README.md`。

## 验证记录

| 验证 | 结果 |
| --- | --- |
| `python -m unittest discover -s tests -q` | 47 项通过；含原有模式回归、假设引用、Jev 题型/范围、编排顺序、DS 措辞输出契约、降级、取消、历史缺口、请求身份、卡片删除及回放边界。 |
| `python -m autochat.cli --input data\dynamic_subtext_demo.json --mode jev-dynamic --thinking disabled --replay-detail --check` | 输入和两项密钥配置检查通过，未调用 API。 |
| 合成样例真实 API 冒烟 | [当前 v2.2 结果](results/dynamic_subtext_jev_judges_v22.json)：3 条有效假设、17 道 Jev 题（含回复方向），DS 2 次、Jev 1 次，各阶段完成，总耗时约 5.9 秒；Jev 选择 `clarify_history`，历史缺口保护生效。 |
| 同输入 A/B/C 合成对照 | Jev 独立判断版的合成对照保存在 `results/comparison_dynamic_subtext_demo_94cb459dcf_*.json`：仅 DS、固定 Jev、动态模式耗时分别约 4.1、4.8、6.4 秒，均选择澄清并给两条候选。这是 v2.1 的流程冒烟，不能由一条合成对话推断质量优劣。 |
| 三个边界合成样例 | [“哦”](results/dynamic_case_08_result.json)没有被断言生气；[原文提示注入](results/dynamic_case_13_result.json)没有让候选宣称已转账。[“我受伤了” v2.2](results/dynamic_case_11_v22_result.json)的 DS 候选改为中性询问，但 Jev 仍将身体受伤假设判为高支持且证据充分，不能视为已消歧。 |
| Tk 可用性冒烟 | 隐藏窗口中实例化侧栏和回放，渲染当前真实动态结果，执行两处“删除卡片”，以及渲染模拟的回复失败卡片，无异常；未做人工视觉检查。 |

上述真实调用只使用新建的合成对话，没有发送 `results/` 中历史私聊。`data/dynamic_subtext_cases.json` 列出任务书要求的多义与提示注入场景及人工期望行为；本轮没有逐项真实 API 盲评。

## 限制

引用校验只证明引文确实来自指定消息或已确认事实，不能证明引用在语义上支持假设。DS 提议不是事实，Jev 的判断也不是独立真值；支持度百分比未经中文聊天领域校准。单句“我受伤了”的合成复核显示，Jev 可能过度确定身体受伤解释；提示词收紧后仍有该问题，界面须保留原文、其他假设和人工审核。关系紧张 Score 是试验性描述量表，不能当作生气概率。历史依据识别仍是启发式规则，复杂指代需要人工审核。当前没有经授权、脱敏的真实私聊评测集，因此尚无真实会话准确率、用户偏好或编辑量结论，也不支持自动发送。

TypeSafe Score 请求与返回字段依据[官方 Score 文档](https://docs.typesafe.ai/primitives/score)核对。
