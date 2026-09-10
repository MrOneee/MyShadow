# DSH research execution

普通聊天、判题、任务管理和发送继续使用 native 工具调用。通用的
`DshEngine.research(messages, ...)` 接受 `web_search`、`web_fetch`、
`search_history`，可通过 `mode='native'|'ptc'` 做同任务对照。
海龟汤的资料发现和明确的资料/群历史整理请求已接到此入口。

`bot.json` 的 `harness.research_mode` 控制该入口的默认模式，缺省为 `native`。
可以设置为 `ptc` 后重启机器人来试用；不要把主聊天的执行模式一起切换。
PTC 是实验选项，是否启用取决于评测，接入本身不意味着更快或更省 Token。

PTC 使用 DSH 的原生 `mode: ptc`、`run_code` 和工具 SDK，代码执行后端为
QuickJS/WASM。模型代码没有 Node、文件系统、进程、环境变量或直接网络访问，
只能调用当前轮提供的工具。不会向代码提供群 ID、数据库句柄或模型密钥。
同一进程仍然只运行一个 DSH 任务；PTC 子调用上限为 2，宿主 Python 回调
仍在调用线程串行处理，以保持 SQLite 和当前群的绑定。

每次代码执行创建独立上下文，默认 QuickJS 堆上限 32 MiB，计算预算 2 秒、
墙钟预算 120 秒、输出上限 24,000 字节。堆上限不等于整个 DSH 进程 RSS。
错误反馈给同一个 Agent 修正，不自动以另一模式重跑。
最终结构化结果仍需通过宿主的 schema 和业务校验；模式参与运行缓存键。

部署需要 `package.json` / `package-lock.json` 中固定版本的依赖。
在此目录运行 `npm ci --ignore-scripts`。依赖只有 JavaScript 和 WASM，
也可将按锁文件安装的 `node_modules` 与代码一起打包部署。

检验：`node --test tests/test_ptc_runtime.mjs`（在项目根目录执行），
以及 Python 的 `tests.test_agent_runtime`、`tests.test_harness_research`。
测评入口见 `benchmarks/ptc_eval.py`。历史工具仍受原有条数、时间范围和
查询次数限制，PTC 不会将它扩展成全量群聊数据库。
