"""Standalone local-only commands; no live-controller integration."""
import argparse
import json
from pathlib import Path

from .benchmark import benchmark, benchmark_markdown
from .bundle import build_bundle, read_json, readiness_markdown
from .fixtures import ram_tail_fixture


def local_path(value: str) -> Path:
    if value.startswith(('\\\\', '//')) or '://' in value:
        raise argparse.ArgumentTypeError('只接受本地磁盘路径，不接受网址或 UNC 网络路径')
    return Path(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='奇美拉独立离线原型：人工模型测试与真实资料缺项检查')
    commands = parser.add_subparsers(dest='command', required=True)
    demo = commands.add_parser('demo', help='运行人工测试链；不会读取真实队伍或游戏状态')
    demo.add_argument('--runs', type=int, default=200)
    demo.add_argument('--seed', type=int, default=71, help='仅用于可重复的离线测试')
    demo.add_argument('--output', type=local_path, default=Path('out/chimera-offline-demo'))
    prepare = commands.add_parser('prepare', help='只读现有导出，生成可携带的资料包与缺项报告')
    prepare.add_argument('--strategy', type=local_path, required=True)
    prepare.add_argument('--hero-catalog', type=local_path, default=Path('cache/chimera-hero-catalog.json'))
    prepare.add_argument('--trial-catalog', type=local_path, default=Path('cache/chimera-ui-catalog.json'))
    prepare.add_argument('--output', type=local_path, default=Path('out/chimera-offline-reference'))
    args = parser.parse_args(argv)
    try:
        input_paths = []
        if args.command == 'demo':
            payload = benchmark(ram_tail_fixture(), args.runs, args.seed)
            document, filename = benchmark_markdown(payload), 'results.json'
        else:
            input_paths = [args.strategy, args.hero_catalog, args.trial_catalog]
            loaded = [read_json(path) for path in input_paths]
            payload = build_bundle(*(item[0] for item in loaded))
            payload['sources'] = [item[1] for item in loaded]
            document, filename = readiness_markdown(payload), 'bundle.json'
        output = args.output.resolve()
        outputs = [output / filename, output / 'report.md']
        resolved_inputs = {path.resolve() for path in input_paths}
        if any(path.resolve() in resolved_inputs for path in outputs):
            raise ValueError('输出会覆盖输入文件，已停止')
        output.mkdir(parents=True, exist_ok=True)
        outputs[0].write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        outputs[1].write_text(document, encoding='utf-8')
        print(f'已生成：{outputs[1]}')
        print('范围：人工模型验证 / 静态资料检查；真实战斗预测尚未实现。')
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f'离线任务未完成：{error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
