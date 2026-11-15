"""Tiny, dependency-free toolkit for animated monospace (ASCII) SVGs.

GitHub shows README images through a sandboxed ``<img>``: CSS and SMIL
animation run, but scripts, web fonts and remote assets never load. This
module keeps those constraints easy to honour:

* ``Grid``          maps (col, row) to pixels and pins every text run to the
                    monospace grid with ``textLength`` so ASCII art stays
                    aligned whichever fallback font the viewer has.
* ``Canvas``        composes sparse character layers (e.g. animation frames).
* ``Choreographer`` turns a looping timeline into uniquely named keyframes.
* ``FrameStrip``    flips through pre-rendered frames like a flip book.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

FONT_STACK = ('ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,'
              '"Liberation Mono","DejaVu Sans Mono",monospace')
CHAR_W = 8.4  # one column at 14px: 0.6em, the common monospace advance

Run = Tuple[str, str]  # (css class or "", text)
Cell = Tuple[int, int]  # (col, row)
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def fmt(value: float) -> str:
    """Compact, deterministic number formatting for SVG/CSS output."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def display_width(text: str) -> int:
    """Terminal column width: East Asian wide/fullwidth characters take two."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _trim_runs(runs: Sequence[Run]) -> Tuple[List[Run], int]:
    """Strip outer spaces across run boundaries; return (runs, leading spaces)."""
    cleaned = [(cls, text) for cls, text in runs if text]
    joined = "".join(text for _, text in cleaned)
    if not joined.strip(" "):
        return [], 0
    lead = len(joined) - len(joined.lstrip(" "))
    end = len(joined.rstrip(" "))
    trimmed: List[Run] = []
    pos = 0
    for cls, text in cleaned:
        start, stop = pos, pos + len(text)
        pos = stop
        lo, hi = max(start, lead), min(stop, end)
        if lo < hi:
            trimmed.append((cls, text[lo - start:hi - start]))
    return trimmed, lead


@dataclass(frozen=True)
class Grid:
    """Monospace character grid; ``y`` is the text baseline of a row."""

    x0: float
    y0: float
    line_h: float = 20.0
    char_w: float = CHAR_W

    def x(self, col: float) -> float:
        return self.x0 + col * self.char_w

    def y(self, row: float) -> float:
        return self.y0 + row * self.line_h

    def row(self, row: float, text: str, cls: str = "", col: float = 0) -> str:
        return self.runs(row, [("", text)], cls=cls, col=col)

    def runs(self, row: float, runs: Sequence[Run], cls: str = "", col: float = 0) -> str:
        trimmed, lead = _trim_runs(runs)
        if not trimmed:
            return ""
        width = sum(display_width(text) for _, text in trimmed)
        # Spans are concatenated with no whitespace: under xml:space="preserve"
        # any gap between them would render as a visible space.
        body = "".join(f'<tspan class="{c}">{esc(t)}</tspan>' if c else esc(t) for c, t in trimmed)
        cls_attr = f' class="{cls}"' if cls else ""
        return (f'<text x="{fmt(self.x(col + lead))}" y="{fmt(self.y(row))}" '
                f'textLength="{fmt(width * self.char_w)}" lengthAdjust="spacing"{cls_attr}>{body}</text>')


class Canvas:
    """Sparse layer of single-column characters addressed by (col, row)."""

    def __init__(self) -> None:
        self._cells: Dict[Cell, Tuple[str, str]] = {}

    def put(self, col: int, row: int, char: str, cls: str = "") -> None:
        if len(char) != 1 or display_width(char) != 1:
            raise ValueError(f"a canvas cell holds one narrow character, got {char!r}")
        if col < 0 or row < 0:
            raise ValueError(f"canvas coordinates must be non-negative, got {(col, row)}")
        self._cells[(col, row)] = (char, cls)

    def text(self, col: int, row: int, text: str, cls: str = "") -> None:
        for offset, char in enumerate(text):
            self.put(col + offset, row, char, cls)

    def render(self, grid: Grid) -> str:
        rows: Dict[int, Dict[int, Tuple[str, str]]] = {}
        for (col, row), cell in self._cells.items():
            rows.setdefault(row, {})[col] = cell
        out = []
        for row in sorted(rows):
            cells = rows[row]
            runs: List[Run] = []
            for col in range(max(cells) + 1):
                char, cls = cells.get(col, (" ", ""))
                if runs and runs[-1][0] == cls:
                    runs[-1] = (cls, runs[-1][1] + char)
                else:
                    runs.append((cls, char))
            out.append(grid.runs(row, runs))
        return "\n".join(part for part in out if part)


Stop = Tuple[float, str, Optional[str]]  # (seconds, declarations, timing function)


@dataclass
class Choreographer:
    """Builds per-element keyframes that all share one looping cycle.

    Every animated element gets class ``an`` (shared duration/looping) plus a
    unique keyframe class. Elements visible only inside a window also get
    ``tr`` so a reduced-motion stylesheet can hide them and show the final
    state instead of every frame stacked on top of each other.
    """

    cycle: float
    prefix: str = "k"
    _rules: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cycle > 0:
            raise ValueError(f"cycle must be positive, got {self.cycle}")
        if not _IDENT.match(self.prefix):
            raise ValueError(f"invalid keyframe prefix {self.prefix!r}")

    def css(self) -> str:
        shared = (f".an{{animation-duration:{fmt(self.cycle)}s;animation-timing-function:linear;"
                  "animation-iteration-count:infinite;animation-fill-mode:both}")
        return shared + "".join(self._rules)

    def reveal(self, t_in: float, t_out: Optional[float] = None, fade: float = 0.12,
               rise: float = 0.0, persist: bool = False) -> str:
        """Fade in at ``t_in``; optionally fade out again at ``t_out``."""
        if fade <= 0:
            raise ValueError("fade must be positive")
        motion = f";transform:translateY({fmt(rise)}px)" if rise else ""
        settled = ";transform:translateY(0px)" if rise else ""
        hidden, shown = "opacity:0" + motion, "opacity:1" + settled
        stops: List[Stop] = [(0, hidden, None), (t_in, hidden, None), (t_in + fade, shown, None)]
        if t_out is None:
            stops.append((self.cycle, shown, None))
        else:
            if t_out < t_in + fade:
                raise ValueError(f"window closes ({t_out}s) before it finishes opening ({t_in + fade}s)")
            stops += [(t_out, shown, None), (t_out + fade, "opacity:0" + motion, None),
                      (self.cycle, "opacity:0" + motion, None)]
        name = self._register(stops)
        return f"an {name}" + (" tr" if t_out is not None and not persist else "")

    def flicker(self, spans: Sequence[Tuple[float, float]], peak: float = 1.0, fade: float = 0.02) -> str:
        """Several brief visibility windows (glitch / scan effects); always transient."""
        if not spans:
            raise ValueError("flicker needs at least one span")
        on, off = f"opacity:{fmt(peak)}", "opacity:0"
        stops: List[Stop] = [(0, off, None)]
        for start, stop in spans:
            if stop < start + fade:
                raise ValueError(f"flicker span {(start, stop)} is shorter than its fade")
            stops += [(start, off, None), (start + fade, on, None), (stop, on, None), (stop + fade, off, None)]
        stops.append((self.cycle, off, None))
        return f"an {self._register(stops)} tr"

    def wipe(self, t0: float, t1: float, distance: float, steps: int,
             hide_at: Optional[float] = None) -> str:
        """Slide a cover right by ``distance`` px in ``steps`` hard jumps."""
        if steps < 1:
            raise ValueError("steps must be >= 1")
        if t1 <= t0:
            raise ValueError(f"wipe must end after it starts ({t0}s -> {t1}s)")
        start, end = "transform:translateX(0px);opacity:1", f"transform:translateX({fmt(distance)}px);opacity:1"
        stops: List[Stop] = [(0, start, None), (t0, start, f"steps({steps},end)"), (t1, end, None)]
        if hide_at is not None:
            gone = f"transform:translateX({fmt(distance)}px);opacity:0"
            stops += [(hide_at, end, None), (hide_at + 0.05, gone, None), (self.cycle, gone, None)]
        else:
            stops.append((self.cycle, end, None))
        return f"an {self._register(stops)}"

    def staircase(self, points: Sequence[Tuple[float, float]], axis: str = "X") -> str:
        """Hold each offset until the next point's time, then jump (token-wise typing, log scroll)."""
        if axis not in ("X", "Y"):
            raise ValueError(f"axis must be X or Y, got {axis!r}")
        if not points:
            raise ValueError("staircase needs at least one point")
        stops: List[Stop] = [(0, f"transform:translate{axis}({fmt(points[0][1])}px)", None)]
        stops += [(t, f"transform:translate{axis}({fmt(v)}px)", "steps(1,end)") for t, v in points]
        stops.append((self.cycle, f"transform:translate{axis}({fmt(points[-1][1])}px)", None))
        return f"an {self._register(stops)}"

    def _register(self, stops: Sequence[Stop]) -> str:
        name = f"{self.prefix}{len(self._rules)}"
        frames: List[Tuple[str, str]] = []
        last_t = 0.0
        for t, decl, timing in stops:
            if t < last_t - 1e-9 or t < 0 or t > self.cycle + 1e-9:
                raise ValueError(f"{name}: keyframe at {t}s is out of order or outside the {self.cycle}s cycle")
            last_t = t
            pct = fmt(100 * t / self.cycle) + "%"
            body = decl + (f";animation-timing-function:{timing}" if timing else "")
            if frames and frames[-1][0] == pct:
                frames[-1] = (pct, body)  # same offset: the later stop wins
            else:
                frames.append((pct, body))
        keyframes = "".join(f"{pct}{{{body}}}" for pct, body in frames)
        self._rules.append(f"@keyframes {name}{{{keyframes}}}.{name}{{animation-name:{name}}}")
        return name


@dataclass(frozen=True)
class FrameStrip:
    """Flip-book: frame ``i`` is visible during slot ``i`` of every period.

    All frames share one keyframe rule; negative delays shift each frame to
    its slot, so the strip is already mid-cycle at load with no blank start.
    Without animation support (or with reduced motion) frame 0 shows.
    """

    name: str
    frames: int
    period: float

    def __post_init__(self) -> None:
        if self.frames < 1 or not self.period > 0:
            raise ValueError("a frame strip needs >= 1 frame and a positive period")
        if not _IDENT.match(self.name):
            raise ValueError(f"invalid frame strip name {self.name!r}")

    def css(self) -> str:
        n, slot = self.name, self.period / self.frames
        rules = [f"@keyframes {n}{{0%{{opacity:1}}{fmt(100 / self.frames)}%{{opacity:0}}100%{{opacity:0}}}}",
                 f".{n}{{opacity:0;animation:{n} {fmt(self.period)}s steps(1,end) infinite}}",
                 f".{n}-0{{opacity:1}}"]
        rules += [f".{n}-{i}{{animation-delay:{fmt(i * slot - self.period)}s}}" for i in range(1, self.frames)]
        return "".join(rules)

    def frame_class(self, index: int) -> str:
        if not 0 <= index < self.frames:
            raise ValueError(f"frame {index} outside 0..{self.frames - 1}")
        return f"fr {self.name} {self.name}-{index}"


def svg_document(width: float, height: float, title: str, desc: str, css: str, body: str,
                 defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(width)}" height="{fmt(height)}" '
            f'viewBox="0 0 {fmt(width)} {fmt(height)}" role="img" aria-labelledby="title desc" '
            f'xml:space="preserve">\n'
            f'<title id="title">{esc(title)}</title><desc id="desc">{esc(desc)}</desc>\n'
            f'<defs>{defs}<style>{css}</style></defs>\n{body}\n</svg>\n')
