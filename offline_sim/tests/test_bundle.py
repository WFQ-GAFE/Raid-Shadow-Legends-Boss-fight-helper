import ast
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from offline_sim.__main__ import main
from offline_sim.bundle import build_bundle, read_json, readiness_markdown


def inputs():
    export = {'format': 'raid-boss-strategy', 'version': 1, 'bossMode': 'chimera',
              'accountId': 'PRIVATE_ACCOUNT', 'pid': 'PRIVATE_PROCESS',
              'strategy': {'name': '测试策略', 'team': {'heroTypeIds': [16]},
                           'objectives': {'mandatoryTrialIds': [8000606], 'minimumDamage': 1},
                           'rules': [{'action': 'DO_NOT_COPY_EXECUTABLE_RULE'}]}}
    heroes = {'heroes': {'10': {'typeId': 10, 'runtimeTypeIds': [10, 16], 'name': '测试英雄',
                               'secret': 'PRIVATE_HERO', 'skills': [{'typeId': 101, 'description': '测试说明',
                                                                  'defaultCooldown': 4, 'pointer': 'PRIVATE_POINTER'}]}}}
    trials = {'difficulties': [{'difficultyId': 6, 'difficulty': 'UltraNightmare', 'trials': [
        {'id': 8000603 + index, 'form': 'Ram', 'part': 'Tail', 'difficultyId': index, 'description': '测试试炼'}
        for index in (1, 2, 3)]}]}
    return export, heroes, trials


class BundleTests(unittest.TestCase):
    def test_aliases_and_prerequisites_without_execution_or_private_fields(self):
        args = inputs()
        before = copy.deepcopy(args)
        bundle = build_bundle(*args)
        self.assertEqual(before, args)
        self.assertEqual(10, bundle['roster'][0]['canonicalTypeId'])
        self.assertEqual([8000604, 8000605, 8000606], [trial['id'] for trial in bundle['requiredTrials']])
        self.assertEqual([8000604, 8000605], bundle['requiredTrials'][-1]['prerequisiteIds'])
        self.assertEqual(6, bundle['requiredTrials'][-1]['battleDifficultyId'])
        self.assertEqual(3, bundle['requiredTrials'][-1]['chainRank'])
        serialized = json.dumps(bundle)
        self.assertNotIn('PRIVATE', serialized)
        self.assertNotIn('DO_NOT_COPY', serialized)
        self.assertFalse(bundle['readiness']['canPredictRealTrials'])
        self.assertFalse(bundle['roster'][0]['skills'][0]['executableModel'])
        self.assertIn('不能预测真实队伍', readiness_markdown(bundle))

    def test_unknown_heroes_and_trials_are_reported_without_fabrication(self):
        export, heroes, trials = inputs()
        export['strategy']['team']['heroTypeIds'] = [987654]
        export['strategy']['objectives']['mandatoryTrialIds'] = [123456]
        bundle = build_bundle(export, heroes, trials)
        self.assertFalse(bundle['roster'][0]['resolved'])
        self.assertEqual([], bundle['requiredTrials'])
        codes = {gap['code'] for gap in bundle['readiness']['gaps']}
        self.assertTrue({'unresolved_trials', 'unresolved_heroes'} <= codes)

    def test_reject_inconsistent_or_malformed_inputs(self):
        for mutate in (lambda e, h, t: e.update(bossMode='hydra'),
                       lambda e, h, t: e.update(version=True),
                       lambda e, h, t: e['strategy'].update(bossMode='hydra'),
                       lambda e, h, t: e['strategy']['team'].update(heroTypeIds=[True]),
                       lambda e, h, t: e['strategy']['team'].update(heroTypeIds=[10] * 6),
                       lambda e, h, t: t['difficulties'][0]['trials'].pop(),
                       lambda e, h, t: h['heroes'].update({'20': {'typeId': 20, 'runtimeTypeIds': [16]}})):
            args = inputs()
            mutate(*args)
            with self.assertRaises(ValueError):
                build_bundle(*args)

    def test_goal_order_does_not_change_dependency_order(self):
        export, heroes, trials = inputs()
        export['strategy']['objectives']['mandatoryTrialIds'] = [8000606, 8000604]
        first = build_bundle(export, heroes, trials)['requiredTrials']
        export['strategy']['objectives']['mandatoryTrialIds'].reverse()
        self.assertEqual(first, build_bundle(export, heroes, trials)['requiredTrials'])

    def test_forged_verified_fields_cannot_enable_real_prediction(self):
        args = inputs()
        args[0].update(evidence='verified', canPredictRealBattle=True)
        args[0]['strategy']['team']['stats'] = {'attack': 99999999}
        bundle = build_bundle(*args)
        self.assertFalse(bundle['readiness']['canPredictRealBattle'])
        self.assertGreaterEqual(len(bundle['readiness']['gaps']), 6)

    def test_json_reader_bom_and_invalid_numeric(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text('{"a":1}', encoding='utf-8-sig')
            data, provenance = read_json(path)
            self.assertEqual({'a': 1}, data)
            self.assertEqual(64, len(provenance['sha256']))
            path.write_text('{"a":NaN}', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_json(path)

    def test_cli_generates_reports_and_rejects_source_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ('bundle.json', 'heroes.json', 'trials.json')]
            for path, payload in zip(paths, inputs()):
                path.write_text(json.dumps(payload), encoding='utf-8')
            command = ['prepare', '--strategy', str(paths[0]), '--hero-catalog', str(paths[1]), '--trial-catalog', str(paths[2])]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(command + ['--output', str(root / 'output')]))
            self.assertTrue((root / 'output' / 'report.md').is_file())
            original = paths[0].read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(command + ['--output', str(root)])
            self.assertEqual(2, error.exception.code)
            self.assertEqual(original, paths[0].read_bytes())

    def test_cli_refuses_urls_and_unc_paths(self):
        for path in ('https://example.invalid/data.json', '\\\\server\\share\\data.json'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['prepare', '--strategy', path])

    def test_runtime_architecture_has_only_reviewed_stdlib_and_package_imports(self):
        allowed = {'__future__', 'random', 'dataclasses', 'typing', 'hashlib', 'json', 'pathlib',
                   'collections', 'statistics', 'argparse'}
        package = Path(__file__).resolve().parents[1]
        modules = {file.stem for file in package.glob('*.py')}
        for source in package.glob('*.py'):
            tree = ast.parse(source.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(item.name.split('.')[0] in allowed for item in node.names), source.name)
                if isinstance(node, ast.ImportFrom):
                    permitted = modules if node.level else allowed
                    self.assertIn((node.module or '').split('.')[0], permitted, source.name)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {'eval', 'exec', '__import__', 'compile'}, source.name)


if __name__ == '__main__':
    unittest.main()
