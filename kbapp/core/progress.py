"""kbapp.core.progress：由职责边界组织的知识库能力。"""
import sys
import time

class Progress:

    def __init__(self, total, prefix='', width=26, stream=None, enabled=True):
        self.total = max(1, int(total))
        self.n = 0
        self.prefix = prefix
        self.width = width
        self.stream = stream or sys.stdout
        self.start = time.time()
        self.last = 0.0
        self.enabled = enabled
        self.note = ''
        self.ascii = False
        try:
            self.tty = bool(getattr(self.stream, 'isatty', lambda: False)())
        except Exception:
            self.tty = False

    @staticmethod
    def _fmt(sec):
        sec = max(0, int(sec))
        if sec >= 3600:
            return f'{sec // 3600}h{sec % 3600 // 60:02d}m'
        if sec >= 60:
            return f'{sec // 60}m{sec % 60:02d}s'
        return f'{sec}s'

    def _bar(self, frac):
        filled = int(self.width * frac)
        if self.ascii:
            return '=' * filled + '-' * (self.width - filled)
        return '█' * filled + '░' * (self.width - filled)

    def _line(self, frac):
        el = time.time() - self.start
        rate = self.n / el if el > 0 else 0
        eta = (self.total - self.n) / rate if rate > 0 else 0
        line = f'{self.prefix}[{self._bar(frac)}] {frac * 100:5.1f}%  {self.n}/{self.total}  {self._fmt(el)}<{self._fmt(eta)}'
        if self.note:
            line += f'  {self.note[:38]}'
        return line

    def _write(self, s, newline=False):
        try:
            self.stream.write(s + ('\n' if newline else ''))
            self.stream.flush()
        except UnicodeEncodeError:
            self.ascii = True
            self.stream.write(self._line(min(1.0, self.n / self.total)) + ('\n' if newline else ''))
            self.stream.flush()

    def update(self, n=1, note=''):
        self.n += n
        if note:
            self.note = note
        if not self.enabled:
            return
        frac = min(1.0, self.n / self.total)
        now = time.time()
        if self.tty:
            if now - self.last < 0.08 and self.n < self.total:
                return
            self.last = now
            self._write('\r' + self._line(frac) + ' ' * 12)
        else:
            step = max(1, self.total // 10)
            if self.n % step == 0 or self.n >= self.total:
                self._write(self._line(frac), newline=True)

    def close(self, note=''):
        if note:
            self.note = note
        if not self.enabled:
            return
        frac = min(1.0, self.n / self.total)
        if self.tty:
            self._write('\r' + self._line(frac) + ' ' * 12, newline=True)
        else:
            self._write(self._line(frac), newline=True)

class StepBar:
    """run.py 用的步骤计数条：打印 [3/9] 名称。"""

    def __init__(self, total, stream=None):
        self.total = total
        self.n = 0
        self.stream = stream or sys.stdout

    def step(self, name):
        self.n += 1
        print(f'\n[{self.n}/{self.total}] {name}', flush=True)
