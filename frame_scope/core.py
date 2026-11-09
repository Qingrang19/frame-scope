"""Independent, offline frame validation and incremental measurement statistics."""
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
import json
import math


@dataclass(frozen=True)
class Frame:
    seq: int
    timestamp: float
    channels: tuple

    def as_dict(self):
        return dict(seq=self.seq, timestamp=self.timestamp, channels=list(self.channels))


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse_frame(line, expected_channels=None):
    try:
        data = json.loads(line, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as exc:
        raise ValueError('invalid JSON: ' + str(exc)) from exc
    if not isinstance(data, dict) or set(data) != {'seq', 'timestamp', 'channels'}:
        raise ValueError('expected exactly seq, timestamp, channels')
    seq, timestamp, channels = data['seq'], data['timestamp'], data['channels']
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError('seq must be a nonnegative integer')
    if not finite_number(timestamp) or timestamp < 0:
        raise ValueError('timestamp must be a finite nonnegative number')
    if not isinstance(channels, list) or not channels:
        raise ValueError('channels must be a nonempty array')
    if not all(finite_number(value) for value in channels):
        raise ValueError('channels must contain only finite numbers (not booleans)')
    if expected_channels is not None and len(channels) != expected_channels:
        raise ValueError(f'channel count changed: expected {expected_channels}, got {len(channels)}')
    return Frame(seq, timestamp, tuple(channels))


@dataclass
class RunningStats:
    count: int = 0
    minimum: Decimal = None
    maximum: Decimal = None
    mean: Decimal = Decimal(0)
    m2: Decimal = Decimal(0)

    def add(self, value):
        value = Decimal(str(value))
        # Decimal avoids overflow for valid finite binary64 inputs and their variance.
        with localcontext() as ctx:
            ctx.prec = 40
            self.count += 1
            self.minimum = value if self.minimum is None else min(self.minimum, value)
            self.maximum = value if self.maximum is None else max(self.maximum, value)
            delta = value - self.mean
            self.mean += delta / self.count
            self.m2 += delta * (value - self.mean)

    def report(self, channel):
        with localcontext() as ctx:
            ctx.prec = 40
            variance = max(Decimal(0), self.m2 / self.count)
            return dict(channel=channel, count=self.count, min=str(self.minimum),
                        max=str(self.maximum), mean=str(self.mean),
                        std_population=str(variance.sqrt()))


@dataclass
class Analyzer:
    lines: int = 0
    blank_lines: int = 0
    invalid_frames: int = 0
    valid_frames: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    forward_gap_events: int = 0
    forward_gap_slots: int = 0
    timestamp_regressions: int = 0
    seen: set = field(default_factory=set)
    high_water: int = None
    last_timestamp: float = None
    stats: list = field(default_factory=list)

    def accept(self, frame):
        self.valid_frames += 1
        if frame.seq in self.seen:
            self.duplicates += 1
        elif self.high_water is not None and frame.seq < self.high_water:
            self.out_of_order += 1
        if self.high_water is not None and frame.seq > self.high_water + 1:
            self.forward_gap_events += 1
            self.forward_gap_slots += frame.seq - self.high_water - 1
        self.high_water = frame.seq if self.high_water is None else max(self.high_water, frame.seq)
        self.seen.add(frame.seq)
        if self.last_timestamp is not None and frame.timestamp < self.last_timestamp:
            self.timestamp_regressions += 1
        self.last_timestamp = frame.timestamp
        if not self.stats:
            self.stats = [RunningStats() for _ in frame.channels]
        for stat, value in zip(self.stats, frame.channels):
            stat.add(value)

    def report(self):
        keys = ('lines', 'blank_lines', 'invalid_frames', 'valid_frames', 'duplicates',
                'out_of_order', 'forward_gap_events', 'forward_gap_slots', 'timestamp_regressions')
        result = {key: getattr(self, key) for key in keys}
        result['channel_count'] = len(self.stats)
        result['channels'] = [stat.report(i) for i, stat in enumerate(self.stats)]
        return result


def scan(stream, analyzer, diagnostic, max_line_chars=1048576):
    """Yield valid frames in arrival order; errors do not alter accepted state."""
    while True:
        line = stream.readline(max_line_chars + 1)
        if not line:
            return
        analyzer.lines += 1
        if len(line) > max_line_chars:
            while not line.endswith('\n'):
                line = stream.readline(max_line_chars + 1)
                if not line:
                    break
            analyzer.invalid_frames += 1
            diagnostic(analyzer.lines, 'line exceeds character limit')
            continue
        if not line.strip():
            analyzer.blank_lines += 1
            continue
        try:
            frame = parse_frame(line, len(analyzer.stats) or None)
        except ValueError as exc:
            analyzer.invalid_frames += 1
            diagnostic(analyzer.lines, str(exc))
            continue
        analyzer.accept(frame)
        yield frame
