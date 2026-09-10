# Security and private configuration

Never commit live credentials or runtime data. The repository ignores the following local files and directories:

- `.env`, `ai.json`, `bot.json` and `*_keys.json`
- WeChat data, SQLite databases, logs, screenshots and backups
- locally generated member memory and source-derived knowledge cards

Start from `.env.example`, `ai.json.example` and `bot.json.example`. Keep API keys, desktop passwords, WeChat account identifiers, database keys and server details only in the ignored local files.

Before publishing a change, inspect the staged files and run a secret scanner. If a credential is ever committed, removing the file in a later commit is insufficient: revoke or rotate the credential and remove it from Git history.
