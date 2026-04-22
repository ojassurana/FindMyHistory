# FindMyHistory

## Project Overview
FindMyHistory is a project to help users find and explore their history.

## Development

### Setup
```bash
# Clone the repo
git clone https://github.com/ojassurana/FindMyHistory.git
cd FindMyHistory
```

## Conventions
- Use descriptive commit messages
- Keep PRs focused and small

## Secrets & Credentials
- All credentials (bot tokens, API keys, etc.) must be stored in a `.env` file — never hardcoded
- `.env` is in `.gitignore` and must never be committed
- Reference secrets via environment variables (e.g. `process.env.BOT_TOKEN`)

## Design
- Prioritize a sleek, polished UI — aesthetics and user experience matter
- Favor clean layouts, smooth interactions, and modern design patterns

## Auto Commit & Push
After making any change to the codebase (no matter how small), automatically commit and push to GitHub. Do not ask for confirmation — just do it.
- Stage the changed files, commit with a concise message, and `git push` immediately.
- This applies to every edit: new files, modifications, deletions, config changes, etc.
