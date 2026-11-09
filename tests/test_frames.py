import io
import json
import subprocess
import sys
import unittest
from frame_scope.core import Analyzer, parse_frame, scan
from frame_scope.__main__ import write_csv


def record(seq=0, timestamp=0, channels=None):
    return json.dumps(dict(seq=seq, timestamp=timestamp, channels=channels if channels is not None else [1, 2]))


def inspect(lines, limit=1048576):
    analyzer, diagnostics = Analyzer(), []
    frames = list(scan(io.StringIO('\n'.join(lines)), analyzer,
                       lambda line, msg: diagnostics.append((line, msg)), limit))
    return analyzer.report(), diagnostics, frames


class SchemaTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_frame(record()).channels, (1, 2))

    def test_bad_schema(self):
        for text in ('[]', '{}', record()[:-1] + ', "extra":1}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_frame(text)

    def test_sequence(self):
        for seq in (-1, 1.2, True, '1'):
            with self.subTest(seq=seq), self.assertRaises(ValueError):
                parse_frame(record(seq=seq))

    def test_timestamp(self):
        for timestamp in (-1, float('nan'), float('inf'), True, '0'):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                parse_frame(record(timestamp=timestamp))

    def test_channels(self):
        for channels in ([], [True], [float('nan')], [float('inf')], ['1'], [10**400]):
            with self.subTest(channels=channels), self.assertRaises(ValueError):
                parse_frame(record(channels=channels))

    def test_duplicate_key(self):
        with self.assertRaises(ValueError):
            parse_frame('{"seq":0,"seq":1,"timestamp":0,"channels":[1]}')

    def test_deep_json_diagnostic(self):
        result, diagnostics, _ = inspect(['[' * 2000 + ']' * 2000])
        self.assertEqual(result['invalid_frames'], 1)
        self.assertEqual(diagnostics[0][0], 1)


class StreamTests(unittest.TestCase):
    def test_line_numbers_blank_and_recovery(self):
        result, errors, frames = inspect(['', '{oops', record(), '  ', record(1)])
        self.assertEqual((result['lines'], result['blank_lines'], result['invalid_frames']), (5, 2, 1))
        self.assertEqual(errors[0][0], 2)
        self.assertEqual(len(frames), 2)

    def test_channel_change_does_not_change_state(self):
        report, errors, _ = inspect([record(), record(100, channels=[1]), record(1)])
        self.assertEqual(report['valid_frames'], 2)
        self.assertEqual(report['forward_gap_slots'], 0)
        self.assertEqual(errors[0][0], 2)

    def test_gap_duplicate_late_semantics(self):
        report, _, _ = inspect([record(seq) for seq in (5, 8, 8, 6, 6, 9)])
        self.assertEqual(report['duplicates'], 2)
        self.assertEqual(report['out_of_order'], 1)
        self.assertEqual(report['forward_gap_events'], 1)
        self.assertEqual(report['forward_gap_slots'], 2)

    def test_timestamp_regression(self):
        report, _, _ = inspect([record(i, t) for i, t in enumerate((1, 1, 0, 2))])
        self.assertEqual(report['timestamp_regressions'], 1)

    def test_population_stats(self):
        report, _, _ = inspect([record(i, channels=[x]) for i, x in enumerate((2, 4))])
        stats = report['channels'][0]
        self.assertEqual(float(stats['mean']), 3)
        self.assertEqual(float(stats['std_population']), 1)
        self.assertEqual(stats['count'], 2)

    def test_extreme_stats_stay_finite(self):
        report, _, _ = inspect([record(0, channels=[-1e308]), record(1, channels=[1e308])])
        self.assertEqual(float(report['channels'][0]['std_population']), 1e308)
        self.assertNotIn('Infinity', json.dumps(report))

    def test_empty(self):
        report, errors, frames = inspect([])
        self.assertEqual(report['valid_frames'], 0)
        self.assertEqual(report['channels'], [])

    def test_line_limit_drains_whole_line(self):
        report, errors, frames = inspect(['x' * 500, record()], 100)
        self.assertEqual(report['lines'], 2)
        self.assertEqual(report['invalid_frames'], 1)
        self.assertEqual(len(frames), 1)

    def test_csv_has_summary_and_stats(self):
        report, _, _ = inspect([record()])
        output = io.StringIO()
        write_csv(report, output)
        self.assertIn('summary,valid_frames,,1', output.getvalue())
        self.assertIn('channel,mean,0,1', output.getvalue())


class CLITests(unittest.TestCase):
    def cli(self, *args, input=None):
        return subprocess.run([sys.executable, '-m', 'frame_scope', *args], input=input,
                              capture_output=True, text=True, check=False)

    def test_demo_check_smoke(self):
        demo = self.cli('demo', '--frames', '10', '--channels', '3')
        checked = self.cli('check', input=demo.stdout)
        self.assertEqual(demo.returncode, 0)
        self.assertEqual(checked.returncode, 0)
        self.assertEqual(json.loads(checked.stdout)['valid_frames'], 10)

    def test_replay_diagnostics_and_status(self):
        result = self.cli('replay', input='bad\n' + record())
        self.assertEqual(result.returncode, 1)
        self.assertIn('line 1:', result.stderr)
        self.assertEqual(json.loads(result.stdout)['seq'], 0)

    def test_missing_file_status(self):
        self.assertEqual(self.cli('check', '/does-not-exist/frame-scope').returncode, 2)

    def test_cli_invalid_options(self):
        self.assertEqual(self.cli('demo', '--frames', '0').returncode, 2)

    def test_error_demo(self):
        demo = self.cli('demo', '--frames', '5', '--errors')
        check = self.cli('check', input=demo.stdout)
        report = json.loads(check.stdout)
        self.assertEqual(check.returncode, 1)
        self.assertEqual(report['invalid_frames'], 1)
        self.assertEqual(report['duplicates'], 1)
        self.assertEqual(report['out_of_order'], 1)
        self.assertEqual(report['forward_gap_slots'], 2)


if __name__ == '__main__':
    unittest.main()
