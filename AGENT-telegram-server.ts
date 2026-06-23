{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "defaultMode": "default",
    "allow": [
      "Read",
      "Glob",
      "Grep",
      "Bash(ls:*)",
      "Bash(cat:*)",
      "Bash(grep:*)",
      "Bash(rg:*)",
      "mcp__postgres__query_data",
      "mcp__postgres__count_rows",
      "mcp__postgres__describe_table"
    ],
    "deny": [
      "Read(/home/<agent>/.ssh/**)",
      "Read(**/.env)",
      "Read(**/.env.*)",
      "Read(**/*.env)",
      "Read(/etc/<agent>/secrets.env)",
      "Read(**/*.key)",
      "Read(**/*.pem)",
      "Read(**/secrets/**)",
      "Read(/home/<agent>/claude/.claude/projects/-home-<agent>-claude/memory/**)",
      "Write(/home/<agent>/.ssh/**)",
      "Write(/home/<agent>/claude/.claude/projects/-home-<agent>-claude/memory/**)",
      "Write(**/.env)",
      "Write(**/.env.*)",
      "Write(**/*.env)",
      "Write(/etc/<agent>/secrets.env)",
      "Write(**/*.key)",
      "Write(**/*.pem)",
      "Write(**/secrets/**)",
      "Write(/etc/**)",
      "Write(/usr/lib/**)",
      "Write(/usr/share/**)",
      "Write(/var/log/**)",
      "Edit(/home/<agent>/.ssh/**)",
      "Edit(**/.env)",
      "Edit(/etc/**)",
      "Bash(rm -rf:*)",
      "Bash(rm -fr:*)",
      "Bash(curl:* | bash:*)",
      "Bash(curl:* | sh:*)",
      "Bash(wget:* | sh:*)",
      "Bash(nc -e:*)",
      "Bash(ssh:* -R:*)",
      "Bash(apt install:*)",
      "Bash(pip install:*)",
      "Bash(npm install -g:*)"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "/home/<agent>/workspace/scripts/hooks/<agent>-pretooluse-hook.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/<agent>/workspace/scripts/hooks/<agent>-stop-hook.py"
          }
        ]
      }
    ]
  }
}
