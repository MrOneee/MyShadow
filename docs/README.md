# MyShadow 文档

按功能归类，一级文件夹内保留原文件名。各功能的设计提案、实现说明和验收记录放在一起；历史文档的内容以其日期和状态说明为准。

## 项目总览

- [MyShadow 完整项目说明](PROJECT_OVERVIEW.md)：定位、架构、运行流程、功能、配置、数据存储、部署运维与限制。

## 架构与性能 · architecture

- [整体架构评估](architecture/ARCHITECTURE_REVIEW.md)
- [数据库读取与内存优化](architecture/MEMORY_OPTIMIZATION.md)
- [人设与输入预算](architecture/prompt-budget.md)

## Agent 执行层 · agent-runtime

- [DSH Harness](agent-runtime/HARNESS.md)
- [DSH 升级验收](agent-runtime/HARNESS_VALIDATION.md)

## 会话与定时任务 · conversation

- [群聊参与决策](conversation/SELECTIVE_REPLY.md)
- [私聊支持](conversation/private-messages.md)
- [定时与周期任务](conversation/SCHEDULED_TASKS.md)

## 记忆 · memory

- [成员画像与关系记忆方案](memory/member-memory-proposal.md)
- [成员记忆实现](memory/member-memory-implementation.md)
- [好感度更新链路修复](memory/RELATIONSHIP_UPDATE_FIX.md)
- [成员记忆验收](memory/member-memory-evaluation.md)
- [记忆版本与迁移记录](memory/MEMORY_VERSIONS.md)
- [LongMemEval / LoCoMo 基线评测](memory/benchmark-report-2026-10-22.md)
- [记忆2.1.0复测与版本对比](memory/memory-2.1-evaluation.md)
- [记忆2.2.0复测与版本对比](memory/memory-2.2-evaluation.md)
- [记忆准确率持续优化目标](memory/memory-goal-plan.md)
- [记忆准确率迭代记录](memory/memory-goal-journal.md)
- [2.3目标迭代报告](memory/memory-2.3-evaluation.md)
- [2.4目标迭代报告](memory/memory-2.4-evaluation.md)
- [2.5目标迭代与独立复测报告](memory/memory-2.5-evaluation.md)
- [2.6长回复优化与新题确认报告](memory/memory-2.6-evaluation.md)
- [2.7证据摘录、日期与复测报告](memory/memory-2.7-evaluation.md)
- [2.8保留上下文的混合读取评测](memory/memory-2.8-evaluation.md)
- [2.9证据组合与读出核对评测](memory/memory-2.9-evaluation.md)
- [2.10候选值标注评测](memory/memory-2.10-evaluation.md)
- [2.10生产部署与回退记录](memory/memory-2.10-production-deployment-2026-10-23.md)
- [统一成员画像](memory/unified-member-profiles.md)
- [梗记忆与调参](memory/MEME_MEMORY.md)

## 人设与背景知识 · personas

- [人格系统与影的口头禅](personas/PERSONA_SYSTEM.md)
- [人设整理记录](personas/PERSONA_NOTES.md)
- [背景知识机制](personas/background-knowledge.md)
- [蒋丞系统提示词](personas/jiangcheng-system-prompt.txt)
- [蒋丞系统消息示例](personas/jiangcheng-system-message-example.txt)

## 读取工具 · tools

- [历史消息搜索](tools/HISTORY_SEARCH.md)
- [图片读取](tools/IMAGE_READING.md)
- [链接内容读取](tools/LINK_READING.md)

## 活动与技能 · activities

- [Skill 活动与海龟汤](activities/SKILL_ACTIVITIES.md)
- [活动框架实现](activities/ACTIVITY_IMPLEMENTATION.md)
