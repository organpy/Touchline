#!/usr/bin/env python3
"""
Scan the codebase for `@app_commands.command` decorators and produce a Markdown file listing each command, its description, and the function signature.

This is a simple text-based generator and does not execute the bot code.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "COMMANDS_DETAILED.md"

DECORATOR_RE = re.compile(r"@app_commands.command\((.+)\)")
NAME_RE = re.compile(r'name\s*=\s*"(?P<name>[^"]+)"')
DESC_RE = re.compile(r'description\s*=\s*"(?P<desc>[^"]+)"')


def scan_file(path: Path):
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        m = DECORATOR_RE.search(line)
        if m:
            args = m.group(1)
            name = (NAME_RE.search(args).group('name') if NAME_RE.search(args) else '')
            desc = (DESC_RE.search(args).group('desc') if DESC_RE.search(args) else '')
            # find the next function def
            func_name = ''
            sig = ''
            for j in range(i+1, min(i+8, len(lines))):
                if lines[j].strip().startswith('async def') or lines[j].strip().startswith('def'):
                    # function signature
                    sig_line = lines[j].strip()
                    func_name = sig_line.split('def',1)[1].split('(')[0].strip()
                    sig = sig_line
                    break
            entries.append({'name': name, 'cog_fn': func_name, 'desc': desc or '' , 'sig': sig})
    return entries


def scan_cogs():
    found = []
    for root, dirs, files in os.walk(ROOT / 'cogs'):
        for file in files:
            if file.endswith('.py'):
                path = Path(root) / file
                found.extend(scan_file(path))
    return sorted(found, key=lambda e: e['name'])


def write_md(entries):
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('# Commands - Detailed List\n\n')
        for e in entries:
            f.write(f"## /{e['name']}\n\n")
            if e['desc']:
                f.write(f"**Description:** {e['desc']}\n\n")
            if e['sig']:
                f.write(f"**Signature:** `{e['sig']}`\n\n")
            f.write('\n')
    print(f"Wrote {OUT}")


if __name__ == '__main__':
    entries = scan_cogs()
    write_md(entries)
