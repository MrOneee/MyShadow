# 人格系统

人格由四类资源组成：`identity_file` 定义身份与长期背景边界，`voice_file` 定义措辞和回复节奏，`policy_file` 提供所有人格共享的工具与安全规则，`background` 指向常驻背景 `core.md` 和按需检索的知识卡。运行时只按可信配置选择人格，聊天内容不能切换人格。

`persona_profiles` 是命名人格注册表，`default_persona` 选择所有未单独配置会话使用的默认人格，`group_personas.<group>.persona_id` 绑定指定群。群项可覆盖名称、别名和上下文预算。旧版在群项中直接提供 `prompt_file` 的配置仍兼容。

```json
{
  "persona_policy_file": "personas/shared.md",
  "default_persona": "creator",
  "persona_profiles": {
    "creator": {
      "name": "影",
      "aliases": ["影"],
      "identity_file": "personas/creator.identity.md",
      "voice_file": "personas/voices/creator.md",
      "background": "knowledge/creator"
    },
    "jiangcheng": {
      "name": "蒋丞",
      "aliases": ["蒋丞", "丞哥"],
      "identity_file": "personas/jiangcheng.identity.md",
      "voice_file": "personas/voices/jiangcheng.md",
      "background": "knowledge/jiangcheng"
    }
  },
  "group_personas": {
    "123456@chatroom": {"persona_id": "jiangcheng"}
  }
}
```

每个知识目录包含：

- `core.md`：每轮常驻的少量稳定事实和关系边界。
- `cards.json`：详细事实、关系、经历、技能和偏好。
- `aliases.json`：触发自动召回和查询扩展的主题别名。
- `index.sqlite`：由 `build_index` 生成的只读检索索引。

创建者知识目录来自私人聊天归档，必须留在私有部署环境，已由 `.gitignore` 排除。公开仓库只保存装配机制和无私人资料的配置示例。
