"""Command-line interface for local operation and HITL resume."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from .database import initialize_database
from .factory import create_procurement_app


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="企业采购决策与执行 Multi-Agent")
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="数据库、Checkpoint 与 Trace 目录"
    )
    parser.add_argument(
        "--without-mcp", action="store_true", help="仅用于隔离诊断；正常运行应启用 MCP"
    )
    parser.add_argument("--model", default=None, help="正式运行使用的 LangChain 模型标识")
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="显式使用离线测试模型；不得用于正式采购决策",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("init-db", help="初始化或重置模拟业务数据库")
    initialize.add_argument("--reset", action="store_true")

    request = subparsers.add_parser("request", help="提交自然语言采购需求")
    request.add_argument("text")
    request.add_argument("--session", default=None)

    approve = subparsers.add_parser("approve", help="批准暂停中的方案")
    approve.add_argument("session")

    reject = subparsers.add_parser("reject", help="拒绝暂停中的方案")
    reject.add_argument("session")

    modify = subparsers.add_parser("modify", help="修改暂停中或已完成会话的采购条件")
    modify.add_argument("session")
    modify.add_argument("change")

    demo = subparsers.add_parser("demo", help="运行验收示例")
    demo.add_argument("--approve", action="store_true", help="分析暂停后立即批准并执行")
    demo.add_argument("--session", default=None)

    subparsers.add_parser("metrics", help="显示当前进程累计指标")
    evaluate = subparsers.add_parser("eval", help="运行固定业务回归评测集")
    evaluate.add_argument("--limit", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "eval":
        from .evaluation import run_evaluation

        try:
            _print(run_evaluation(limit=args.limit).as_dict())
            return 0
        except Exception as exc:  # noqa: BLE001 - CLI owns the final error boundary
            _print({"status": "error", "error_type": type(exc).__name__, "message": str(exc)})
            return 1
    if args.command == "init-db":
        try:
            root = (
                args.data_dir.resolve()
                if args.data_dir
                else Path(__file__).resolve().parents[1] / "data"
            )
            initialize_database(root / "procurement.sqlite", reset=args.reset)
            _print({"status": "initialized", "data_dir": str(root)})
            return 0
        except Exception as exc:  # noqa: BLE001 - CLI owns the final error boundary
            _print({"status": "error", "error_type": type(exc).__name__, "message": str(exc)})
            return 1

    app = None
    try:
        app = create_procurement_app(
            data_dir=args.data_dir,
            enable_mcp=not args.without_mcp,
            model=args.model,
            deterministic=args.deterministic,
        )
        if args.command == "request":
            _print(app.submit(args.text, session_id=args.session).as_dict())
        elif args.command == "approve":
            _print(app.approve(args.session).as_dict())
        elif args.command == "reject":
            _print(app.reject(args.session).as_dict())
        elif args.command == "modify":
            _print(app.modify(args.session, args.change).as_dict())
        elif args.command == "demo":
            session = args.session or f"demo-{uuid.uuid4().hex[:8]}"
            response = app.submit(
                "下个月需要采购500台设备，预算80万，月底前必须到货，帮我确定最合适的采购方案。",
                session_id=session,
            )
            _print(response.as_dict())
            if args.approve and response.status == "approval_required":
                _print(app.approve(session).as_dict())
        elif args.command == "metrics":
            _print(app.metrics())
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI owns the final stable error boundary
        _print({"status": "error", "error_type": type(exc).__name__, "message": str(exc)})
        return 1
    finally:
        if app is not None:
            app.close()


if __name__ == "__main__":
    sys.exit(main())
