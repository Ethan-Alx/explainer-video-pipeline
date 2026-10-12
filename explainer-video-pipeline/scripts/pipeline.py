#!/usr/bin/env python3
"""Portable, native and editable science-video draft pipeline. No GUI or network."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compact', action='store_true', help='Print concise status; save full diagnostics to report or output')
    parser.add_argument('--result-report', type=Path, help='Save complete result for any command')
    parser.add_argument('--deps', type=Path, help='Optional existing dependency directory; normally use requirements.txt')
    sub = parser.add_subparsers(dest='command', required=True)
    p=sub.add_parser('doctor', help='Read-only runtime/media/native-library check')
    p.add_argument('--require-video-tools',action='store_true',help='Treat missing/unusable FFmpeg as a required dependency')
    p = sub.add_parser('init', help='Create project and lock original sources; no image generation')
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--name', required=True)
    p.add_argument('--audio', type=Path, required=True)
    p.add_argument('--srt', type=Path, required=True)
    p.add_argument('--manuscript', type=Path)
    p.add_argument('--width', type=int, default=1920)
    p.add_argument('--height', type=int, default=1080)
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--no-subtitles', action='store_true', help='Retain SRT for timing; output no narration subtitle track')
    p = sub.add_parser('validate', help='Read-only input plan or independently inspect built native draft')
    p.add_argument('--plan', type=Path)
    p.add_argument('--draft', type=Path)
    p.add_argument('--report', type=Path)
    p = sub.add_parser('build', help='Build a new self-contained editable draft; refuses overwrite')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('install', help='Add a new relocated copy to an explicit library/index; editor must be closed')
    p.add_argument('--draft', type=Path, required=True)
    p.add_argument('--library', type=Path, required=True)
    p.add_argument('--index', type=Path, required=True)
    p.add_argument('--name', help='Optional distinct installed name; omission generates a unique name')
    p.add_argument('--dry-run', action='store_true', help='Read-only checks and proposed import; no editor/index writes')
    p = sub.add_parser('relocate', help='Make a new portable copy and rebase native paths; refuses overwrite')
    p.add_argument('--draft', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    from draft_engine import configure, doctor, init_project, validate_plan, validate_draft, build, install, write
    try:
        configure(args.deps)
        if args.command == 'doctor':
            result = doctor(args.require_video_tools)
        elif args.command == 'init':
            result = init_project(args.project, args.name, args.audio, args.srt, args.manuscript, args.width, args.height, args.fps, render_subtitles=not args.no_subtitles)
        elif args.command == 'validate':
            if not args.plan and not args.draft:
                parser.error('validate requires --plan or --draft')
            result = validate_draft(args.draft, args.plan) if args.draft else validate_plan(args.plan)
            if args.plan:
                from production_policy import audit_file
                policy = audit_file(args.plan, 'final')
                result['production_policy'] = policy
                if policy.get('errors'):
                    result['ok'] = False
            if args.report:
                write(args.report, result)
        elif args.command == 'build':
            from production_policy import audit_file
            policy = audit_file(args.plan, 'final')
            if policy.get('errors'):
                result = {'ok': False, 'scope': 'production_policy_pre_build', 'production_policy': policy}
                if args.result_report:
                    write(args.result_report, result)
                print(json.dumps({'ok': False, 'command': 'build', 'errors': len(policy['errors']), 'report': str(args.result_report) if args.result_report else None}, ensure_ascii=False) if args.compact else json.dumps(result, ensure_ascii=False, indent=2))
                return 1
            result = build(args.plan, args.output)
            result['production_policy'] = policy
        elif args.command == 'relocate':
            from portability import relocate
            result = relocate(args.draft, args.output)
        elif args.dry_run:
            from portability import install_preview
            result = install_preview(args.draft, args.library, args.index, args.name)
        else:
            result = install(args.draft, args.library, args.index, args.name)
        if args.result_report:
            write(args.result_report, result)
        if args.compact:
            if not args.result_report and args.command not in ('build', 'init') and not getattr(args, 'report', None):
                parser.error('--compact requires --result-report or command --report for complete diagnostics')
            summary = {k:result.get(k) for k in ('ok','scope','counts','draft_path','project','installed_path','elapsed_seconds') if k in result}
            print(json.dumps(summary, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok', True) else 1
    except (ValueError, KeyError, TypeError, FileNotFoundError, FileExistsError, ImportError, OSError) as exc:
        print(json.dumps({'ok': False, 'command': args.command, 'error': str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
