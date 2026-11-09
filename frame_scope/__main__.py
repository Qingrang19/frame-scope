"""Command-line interface; no network or hardware access."""
import argparse
import csv
import json
import math
import sys
from contextlib import nullcontext
from .core import Analyzer, scan


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def write_csv(report, output):
    writer = csv.writer(output)
    writer.writerow(['section', 'metric', 'channel', 'value'])
    for key, value in report.items():
        if key != 'channels':
            writer.writerow(['summary', key, '', value])
    for channel in report['channels']:
        for key, value in channel.items():
            if key != 'channel':
                writer.writerow(['channel', key, channel['channel'], value])


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline measurement frame inspection and replay')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('check', 'replay'):
        child = sub.add_parser(command)
        child.add_argument('input', nargs='?', default='-', help='UTF-8 NDJSON file or - for stdin')
        child.add_argument('--max-line-chars', type=positive, default=1048576)
        if command == 'check':
            child.add_argument('--format', choices=('json', 'csv'), default='json')
    demo = sub.add_parser('demo')
    demo.add_argument('--frames', type=positive, default=100)
    demo.add_argument('--channels', type=positive, default=4)
    demo.add_argument('--errors', action='store_true', help='append known malformed/integrity cases')
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            for i in range(args.frames):
                frame = dict(seq=i, timestamp=round(i / 100, 6),
                             channels=[round(math.sin(i / 10 + ch), 6) for ch in range(args.channels)])
                print(json.dumps(frame, allow_nan=False))
            if args.errors:
                print('{broken-json')
                print(json.dumps(dict(seq=args.frames - 1, timestamp=0, channels=[0] * args.channels)))
                print(json.dumps(dict(seq=args.frames + 2, timestamp=1, channels=[0] * args.channels)))
                print(json.dumps(dict(seq=args.frames, timestamp=2, channels=[0] * args.channels)))
            return 0
        analyzer = Analyzer()
        source = nullcontext(sys.stdin) if args.input == '-' else open(args.input, encoding='utf-8')
        with source as stream:
            for frame in scan(stream, analyzer,
                              lambda line, msg: print(f'line {line}: {msg}', file=sys.stderr),
                              args.max_line_chars):
                if args.command == 'replay':
                    print(json.dumps(frame.as_dict(), allow_nan=False))
        report = analyzer.report()
        if args.command == 'check':
            if args.format == 'csv':
                write_csv(report, sys.stdout)
            else:
                print(json.dumps(report, indent=2, allow_nan=False))
        return 1 if any(report[key] for key in ('invalid_frames', 'duplicates', 'out_of_order',
                        'forward_gap_events', 'timestamp_regressions')) else 0
    except (OSError, UnicodeError) as exc:
        print(f'frame-scope: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
