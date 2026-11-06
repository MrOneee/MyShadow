# 执行约定与环境差异

此文件记录正式评分前已确定的环境差异，配合评测方案阅读。

- 预选实际数量为 LongMemEval 28 题、LoCoMo 98 题。LoCoMo 索引 1 的对话没有类别 3（常识/开放域）题目，按方案不补选。
- 全部 28 个 LongMemEval 历史含 1,350 个会话、6,874 条用户消息；LoCoMo 全部 10 个对话含 5,882 条发言。输入使用完整历史，生产 observe 的截断仍生效。
- API `/models` 返回 deepseek-flash、deepseek-v4-pro。本轮回答、提取与裁判固定 deepseek-flash，temperature=0、thinking=disabled。模型响应也记录实际 model 字段。连接测试不进入评测统计。
- 本机为 Windows / Python 3.14，未安装 DSH。使用隔离的原生工具循环调用同一 MemoryService.context / handler / update_once，使用项目 READ_POLICY；每题最多两次回忆、最多四轮模型响应。测试不覆盖完整 Bot、DSH、短期摘要、历史搜索、人格系统和梗记忆。
- LoCoMo 映射两名人类成员，每个独立绑定读取函数；可读取两位成员各自的自动上下文，两者共用最多两次检索预算。这是第三人称基准问题的显式授权适配；其可见信息比生产中“当前提问者 + 相关成员三条摘要”的默认入口更宽。
- LongMemEval 每条用户发言和紧随的助手回复合并为一个 exchange；末尾没有回复时用 message，不重复写一遍 message。它重放公开数据中的已完成对话，不是实际发送确认测试。
- 原始历史日期解析为 Asia/Shanghai；历史启用门槛设为 0。队列的 received/lease 字段用于推进离线调度，原始 created 不改为当前时间。事实 recorded_at 仍由生产提取器写为处理时间，正是重放历史的限制之一。
- Hugging Face 主站从本环境连接超时，因此 LongMemEval 从 `https://hf-mirror.com/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json` 下载。不是自制/二次清洗数据；保存原始文件 SHA-256，但未取得主站独立校验。LoCoMo 与评分源码直接从作者 GitHub raw 下载。
- 官方评分函数通过 AST 仅加载需要的定义，避免安装与本任务无关的 BERTScore 和 OpenAI SDK。LoCoMo 使用原始 PorterStemmer 与分类规则；英语正则归一化使用 Python re（本数据所用规则与原 regex 一致）。语义裁判使用官方 LongMemEval 提示、替代模型，LoCoMo 的语义裁判是附加诊断，不是原官方分数。
- 八个独立样例工作线程并行；各自独立数据库。同一样例按时间顺序逐会话处理，问题与对照均独立；已有结果只断点续跑、不按分数择优。API 传输错误最多尝试三次，错误记录保留。
- 未使用设计模式：直接实现更简单。评测脚本不修改生产记忆算法。
