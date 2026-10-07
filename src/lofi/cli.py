#!/usr/bin/env python3
"""lofi — 單一 CLI 入口。

用法:
  lofi generate  [batch_generate 參數]
  lofi qc        [auto_qc 參數]
  lofi library   build | query | summary | tag | set-status
  lofi meta      [make_meta 參數]
  lofi publish   [upload_status 參數]
  lofi backfill  [backfill_catalog 參數]

未安裝套件時也可用: python3 -m lofi.cli <command> ...
"""
import sys

from lofi import backfill, generate, library, meta, migrate, publish, qc

COMMANDS = {
    "generate": generate,
    "qc": qc,
    "library": library,
    "meta": meta,
    "publish": publish,
    "backfill": backfill,
    "migrate": migrate,
}

HELP = "用法: lofi <command> [options]\n\ncommands:\n" + "\n".join(
    f"  {name:<10} {mod.__doc__.strip().splitlines()[0] if mod.__doc__ else ''}"
    for name, mod in COMMANDS.items()
)


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(HELP)
        return
    cmd = argv[0]
    if cmd not in COMMANDS:
        sys.exit(f"未知指令: {cmd}\n可用: {', '.join(COMMANDS)}")
    sys.argv = [f"lofi {cmd}"] + argv[1:]
    COMMANDS[cmd].main()


if __name__ == "__main__":
    main()
