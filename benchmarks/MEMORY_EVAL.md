# LongMemEval / LoCoMo 离线记忆评测

当前工作区正按准确率目标持续迭代，当前版本见 `myshadow/member_retrieval.py: VERSION`，每轮状态见[目标记录](../docs/memory/memory-goal-journal.md)。首次评测脚本与生产记忆代码保存在基线 `source-snapshot.zip`，后续版本各自另有冻结快照；不能用当前代码声称重跑了旧版。新旧版本及迁移说明见 `docs/memory/MEMORY_VERSIONS.md`。

目标迭代使用固定160题的可回答分母36/92。`--conditions memory`仅做记忆条件筛查，默认仍运行三条件；不得把未重跑的none/oracle当同期控制。只读修改可用 `memory_goal_iteration.py` 复制旧库并冻结，完成后使用 `memory_goal_report.py`、`memory_eval_analyze.py`、`memory_goal_verify.py` 导出、比较和核验。写入修改须从干净库完整重放。筛查达标后仍需独立重复及封存新题确认（2.5为34题，自2.6起68题；新题可回答分母24/32，至少17/20题正确），见[实验协议](../docs/memory/memory-goal-plan.md)。

先读 `docs/memory/benchmark-plan-2026-10-22.md` 和同目录执行说明。此工具只导入 MemoryService，不导入 Bot，不操作微信。

## 运行

依赖 Python 3.10+；Windows 通常还需 `python -m pip install tzdata`。其他评测依赖使用标准库；实际生产模块及其依赖须可导入。

```powershell
python benchmarks/memory_eval.py prepare --data .remote-new-memory-eval
python -m unittest tests.test_memory_benchmark tests.test_member_memory tests.test_unified_profiles tests.test_member_retrieval tests.test_memory_context_wiring -q
python benchmarks/memory_eval.py run --data .remote-new-memory-eval --output .remote-new-memory-eval/run --ai .remote-memory-eval/ai.json --workers 8
python benchmarks/memory_eval.py summarize --output .remote-new-memory-eval/run
python benchmarks/memory_eval_analyze.py --data .remote-new-memory-eval --output .remote-new-memory-eval/run --destination benchmarks/new-memory-eval
```

`ai.json` 采用项目配置字段 `base_url`、`api_key`、`model`，放在忽略目录中，不提交密钥。客户端请求直接连接配置的 HTTPS 服务；不使用本机代理。当前实验模型为 deepseek-flash，提取/回答/裁判一致。

prepare 下载原始数据、作者评分脚本和 NLTK PorterStemmer，生成固定随机抽样清单及 SHA-256。审阅清单后再运行；已有正式结果时不要重新 prepare。正式运行校验数据、评分源码、记忆模块和评测脚本哈希。运行开始后不要切换模型或修改脚本。复现既有结果须使用该次 manifest 所对应的输入文件，不能把以后下载的不同版本当成同一次实验。

run 可断点续跑，已经完成的逐题答案与评分不会重复请求。失败记录也保留，不会自动重抽题或筛掉低分。需要全新重复实验时改用新的 `--output` 目录，保留原结果。更改模型时也必须使用新的输出目录。

2.1.0本次正式运行额外包含预先固定的34题留出，共160题。上面的prepare默认只选择原126题；要严格复现本次160题，应在下载完成后使用 `benchmarks/memory-eval-2.1-2026-10-22/manifest.json` 替换新目录的manifest，且必须通过其中的数据/源码哈希检查。不要覆盖已有基线或正式运行目录中的manifest。

本工作区保留了2.1.0运行数据；以下旧命令须先恢复到其冻结快照所在的隔离代码目录，不能在2.2工作区直接执行并声称复现旧版：

```powershell
python benchmarks/memory_eval.py run --data .remote-memory-v21/data --output .remote-memory-v21/repeat-01 --ai .remote-memory-eval/ai.json --workers 8
python benchmarks/memory_compare.py
```

comparison默认比较首次126题基线、本轮正式run-final以及独立运行的旧版34题留出控制；它验证新版本480份、旧版留出102份结果齐全，缺失/评分错误时拒绝生成完整对比。再次运行比较应显式传入新的 `--run` 输出目录。

## 2.2.0复测

2.2.0沿用160题作为回归集，另固定34道新问题，共194题/582份三条件结果；2.1.0冻结实现重新运行新增34题，得102份控制结果。上一轮34题不再是盲测。LoCoMo新增问题仍在原10个对话中，不是新对话验证。具体方案见 `docs/memory/memory-2.2-plan.md`。

```powershell
python benchmarks/memory_eval.py run --data .remote-memory-v22/data --output .remote-memory-v22/repeat-01 --ai .remote-memory-eval/ai.json --workers 8
python benchmarks/memory_eval_analyze.py --data .remote-memory-v22/data --output .remote-memory-v22/run --destination benchmarks/memory-eval-2.2-2026-10-22
python benchmarks/memory_compare_v22.py
```

运行前确保当前生产代码/脚本与该次manifest匹配。`prepare`默认仍生成原126题；重现194题须使用2.2交付manifest及其指定数据文件，不能重新随机抽样。比較脚本要求582份新结果、480份2.1回归对照和102份新留出控制完整且无错误。新增候选与条数上限改变，但自动检索次数、最多两次主动回忆、6500字符预算不变。

## 输出

- `.remote-memory-eval/manifest.json`：抽样、数据/源码哈希。
- `.remote-memory-eval/run/stores/`：独立样例数据库及写入统计。
- `.remote-memory-eval/run/answers/`：逐题回答、自动上下文、实际工具调用。
- `.remote-memory-eval/run/scores/`：官方规则分与替代模型语义裁判分。
- `.remote-memory-eval/run/calls.jsonl`：每次实际 API 请求的用量、延迟和失败；不含密钥。
- `benchmarks/memory-eval-2026-10-22/`：分析工具导出的审阅资料。原始数据不复制进仓库。

LongMemEval 使用作者原始裁判提示，但裁判模型不同于论文，不是官方同配置成绩。LoCoMo 类别 1–4 为官方分类 F1，类别 5 为固定拒答短语命中率；合并均值是混合分，不能统一称为 F1。附加的语义正确率单独报告。

分析工具的来源覆盖率通过已返回文本反查事实/经历来源；重复文本可能对应多条来源，因此是偏乐观的诊断，不能当成官方 Recall@K。即使命中正确会话，其中关键事实仍可能已被截断。

评分使用同一回答模型作为裁判，有相关偏差；报告包含代表性人工复核。公开英文基准与中文群聊画像产品存在范围差异。生产的敏感过滤、身份隔离和事实类别限制均保持开启。
