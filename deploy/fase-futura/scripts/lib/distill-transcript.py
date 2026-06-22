#!/usr/bin/env python3
# chmod +x /home/<agent>/workspace/scripts/lib/distill-transcript.py
"""Extract and clean a <Agent> session transcript from a JSONL file.

Usage: python3 distill-transcript.py <path-to-session.jsonl>

Output (stdout):
    TOTAL_TURNS: N
    ========================================
    IVAN: <message>

    <AGENT>: <message>
    ...

Exit codes: 0 = success, 1 = file not found or unreadable
"""

import json
import re
import sys


def clean(text):
    text = re.sub(r'<channel[^>]*>(.*?)</channel>', lambda m: m.group(1).strip(), text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def extract(path):
    try:
        f = open(path)
    except FileNotFoundError:
        print(f"ERROR: transcript not found: {path}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"ERROR: cannot read transcript: {e}", file=sys.stderr)
        sys.exit(1)

    msgs = []
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get('type') not in ('user', 'assistant'):
                continue
            content = obj.get('message', {}).get('content', '')
            label = 'IVAN' if obj.get('type') == 'user' else '<AGENT>'
            if isinstance(content, str):
                text = clean(content)
                if text:
                    msgs.append(f"{label}: {text}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        text = clean(block.get('text', ''))
                        if text:
                            msgs.append(f"{label}: {text}")

    print(f"TOTAL_TURNS: {len(msgs)}")
    print("=" * 40)
    print("\n\n".join(msgs))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: distill-transcript.py <path-to-session.jsonl>", file=sys.stderr)
        sys.exit(1)
    extract(sys.argv[1])
