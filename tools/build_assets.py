"""Generate the animated ASCII SVGs used by the profile README.

    python3 tools/build_assets.py          # (re)writes assets/*.svg
    python3 -m unittest discover -s tools  # tests, incl. "assets are up to date"

Every scene is a pure function returning an SVG string: deterministic (seeded
RNG), script-free and self-contained, so GitHub's sandboxed <img> renders it.
Edit the data tables below to change wording; layout code rarely needs touching.
"""
from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from itertools import cycle as round_robin
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from svgkit import (
    CHAR_W,
    FONT_STACK,
    Canvas,
    Cell,
    Choreographer,
    FrameStrip,
    Grid,
    Run,
    display_width,
    esc,
    fmt,
    svg_document,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WIDTH = 880

PAL = {
    "bg": "#0b0f17", "bar": "#111826", "line": "#253047",
    "fg": "#e6edf3", "mu": "#8b949e", "dm": "#334055", "sh": "#3b4766",
    "cy": "#22d3ee", "vi": "#a78bfa", "pk": "#f472b6", "gr": "#3fb950", "am": "#e3b341",
}


# --------------------------------------------------------------------------- #
# Shared chrome
# --------------------------------------------------------------------------- #
def base_css() -> str:
    colors = "".join(f".{k}{{fill:{PAL[k]}}}" for k in ("fg", "mu", "dm", "sh", "cy", "vi", "pk", "gr", "am"))
    return (f"text{{font-family:{FONT_STACK};font-size:14px;fill:{PAL['fg']};white-space:pre}}{colors}"
            ".b{font-weight:700}"
            "@keyframes blink{50%{opacity:0}}.blink{animation:blink 1.1s steps(1,end) infinite}"
            "@keyframes pulse{50%{opacity:.3}}.pulse{animation:pulse 2.4s ease-in-out infinite}"
            "@keyframes trace{to{stroke-dashoffset:-100}}.trace{animation:trace 7s linear infinite}"
            "@media (prefers-reduced-motion:reduce){.an,.fr,.blink,.pulse,.scroll,.trace{animation:none!important}"
            ".cover,.tr{display:none}}")


class Scene:
    """Collects <defs> and body fragments for one SVG; hands out unique ids."""

    def __init__(self) -> None:
        self.defs: List[str] = []
        self.body: List[str] = []
        self._next_id = 0

    def uid(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}{self._next_id}"

    def add(self, *parts: str) -> None:
        self.body.extend(part for part in parts if part)


def group(cls: str, parts: Sequence[str]) -> str:
    return f'<g class="{cls}">' + "\n".join(p for p in parts if p) + "</g>"


def window_chrome(width: float, height: float, title: str, live: bool = False) -> Tuple[str, str, str]:
    """macOS-style terminal window. Returns (defs, background, overlay)."""
    w, h = fmt(width), fmt(height)
    defs = (f'<clipPath id="win"><rect width="{w}" height="{h}" rx="12"/></clipPath>'
            '<radialGradient id="glow" cx="50%" cy="0%" r="75%">'
            f'<stop offset="0" stop-color="{PAL["cy"]}" stop-opacity=".08"/>'
            f'<stop offset="1" stop-color="{PAL["cy"]}" stop-opacity="0"/></radialGradient>'
            '<pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse">'
            '<rect width="4" height="1" fill="#fff" fill-opacity=".018"/></pattern>')
    dots = "".join(f'<circle cx="{22 + 20 * i}" cy="18" r="6" fill="{c}"/>'
                   for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")))
    background = (f'<g clip-path="url(#win)"><rect width="{w}" height="{h}" fill="{PAL["bg"]}"/>'
                  f'<rect width="{w}" height="36" fill="{PAL["bar"]}"/>'
                  f'<path d="M0 36.5H{w}" stroke="{PAL["line"]}"/></g>{dots}'
                  f'<text x="{fmt(width / 2)}" y="23" text-anchor="middle" class="mu" '
                  f'style="font-size:12px">{esc(title)}</text>')
    if live:
        background += (f'<circle cx="{fmt(width - 62)}" cy="18" r="4" class="gr pulse"/>'
                       f'<text x="{fmt(width - 52)}" y="22" class="mu" style="font-size:12px">live</text>')
    overlay = (f'<g clip-path="url(#win)"><rect width="{w}" height="{h}" fill="url(#glow)"/>'
               f'<rect width="{w}" height="{h}" fill="url(#scan)"/></g>'
               f'<rect x=".5" y=".5" width="{fmt(width - 1)}" height="{fmt(height - 1)}" rx="11.5" '
               f'fill="none" stroke="{PAL["line"]}"/>')
    return defs, background, overlay


def typing_cover(scene: Scene, grid: Grid, row: float, col: float, n_cols: int, motion: str, *,
                 cursor: bool = False, blink: bool = False, track: str = "") -> str:
    """Opaque cover sliding right over ``n_cols`` characters to "type" them.

    It is clipped to the typed span, so it never hides neighbouring text. An
    optional ``track`` glyph rides on the cover: moving one column per step,
    the repeated glyph looks stationary (used for progress-bar tracks).
    """
    span = n_cols + (1 if cursor else 0)
    x, top, band = grid.x(col), grid.y(row) - 14.5, 19.5
    clip = scene.uid("c")
    scene.defs.append(f'<clipPath id="{clip}"><rect x="{fmt(x)}" y="{fmt(top)}" '
                      f'width="{fmt(span * grid.char_w)}" height="{fmt(band)}"/></clipPath>')
    inner = [f'<rect x="{fmt(x)}" y="{fmt(top)}" width="{fmt(span * grid.char_w)}" height="{fmt(band)}" '
             f'fill="{PAL["bg"]}"/>']
    if track:
        inner.append(grid.row(row, track * n_cols, cls="dm", col=col))
    if cursor:
        inner.append(f'<rect x="{fmt(x)}" y="{fmt(top + 2)}" width="{fmt(grid.char_w)}" height="16" '
                     f'class="cy{" blink" if blink else ""}"/>')
    return f'<g clip-path="url(#{clip})"><g class="{motion} cover">{"".join(inner)}</g></g>'


def meter_runs(filled: int, total: int, glyph: str = "■") -> List[Run]:
    """btop-style meter: cool → hot colour ramp, dim remainder."""
    runs: List[Run] = []
    for i in range(total):
        cls = "dm" if i >= filled else ("cy" if i < total * 0.5 else "vi" if i < total * 0.8 else "pk")
        if runs and runs[-1][0] == cls:
            runs[-1] = (cls, runs[-1][1] + glyph)
        else:
            runs.append((cls, glyph))
    return runs


# --------------------------------------------------------------------------- #
# Hero: terminal boot sequence + ASCII banner + whoami
# --------------------------------------------------------------------------- #
BANNER_FONT: Dict[str, Tuple[str, ...]] = {  # "ANSI Shadow", six rows per glyph
    "W": ("██╗    ██╗", "██║    ██║", "██║ █╗ ██║", "██║███╗██║", "╚███╔███╔╝", " ╚══╝╚══╝ "),
    "H": ("██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"),
    "I": ("██╗", "██║", "██║", "██║", "██║", "╚═╝"),
    "T": ("████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "),
    "E": ("███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"),
    "C": (" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"),
    "N": ("███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"),
}

BOOT_LINES = (  # (label, filler, result, start s, fill duration s)
    ("loading weights", "bar", "7B params of curiosity", 2.1, 1.2),
    ("mounting distributed storage", "dots", "3/3 replicas in sync", 3.7, 0.45),
    ("warming up the borrow checker", "dots", "0 panics · 0 data races", 4.6, 0.45),
)
FILL_COLS = 18

WHOAMI: Tuple[Tuple[str, Tuple[Run, ...]], ...] = (
    ("whoami", (("", "Wen-hui Cai · "), ("fg b", "ccinn"), ("", " · a.k.a. "), ("pk", "白菜"))),
    ("stack", (("", "Rust · Go · C/C++ · PHP"), ("mu", "   # PHP is my native tongue"))),
    ("focus", (("", "distributed storage · data pipelines · AI tooling"),)),
    ("motto", (("am", '"To know but not to do is not to know."'),)),
)


def banner(word: str) -> List[str]:
    missing = sorted({ch for ch in word if ch not in BANNER_FONT})
    if missing:
        raise ValueError(f"no banner glyph for {missing}; add it to BANNER_FONT")
    return ["".join(BANNER_FONT[ch][row] for ch in word) for row in range(6)]


def shadow_runs(row: str) -> List[Run]:
    """Solid blocks take the animated gradient; box-drawing shadow stays dim."""
    runs: List[Run] = []
    for ch in row:
        cls = "bn" if ch == "█" else ("" if ch == " " else "sh")
        if runs and runs[-1][0] == cls:
            runs[-1] = (cls, runs[-1][1] + ch)
        else:
            runs.append((cls, ch))
    return runs


def hero_svg() -> str:
    width, cycle = WIDTH, 20.0
    choreo, scene = Choreographer(cycle), Scene()
    log = Grid(x0=28, y0=72, line_h=21)

    cmd = "./ccinn --boot --mode=ai"
    type_end = 0.6 + 0.055 * len(cmd)
    scene.add(log.runs(0, [("gr b", "$ "), ("", cmd)]),
              typing_cover(scene, log, 0, 2, len(cmd),
                           choreo.wipe(0.6, type_end, len(cmd) * log.char_w, len(cmd), hide_at=2.0), cursor=True))

    filler_col = 38
    for row, (label, filler, result, start, fill) in enumerate(BOOT_LINES, start=1):
        done = start + 0.1 + fill
        parts = [log.row(row, "[ .. ]", cls=f"am {choreo.reveal(0, t_out=done - 0.12)}"),
                 log.row(row, "[ ok ]", cls=f"gr b {choreo.reveal(done)}"),
                 log.row(row, label, col=7)]
        if filler == "bar":
            parts.append(log.runs(row, meter_runs(FILL_COLS, FILL_COLS, "█"), col=filler_col))
        else:
            parts.append(log.row(row, "·" * FILL_COLS, cls="mu", col=filler_col))
        parts.append(typing_cover(scene, log, row, filler_col, FILL_COLS,
                                  choreo.wipe(start + 0.1, done, FILL_COLS * log.char_w, FILL_COLS),
                                  track="░" if filler == "bar" else ""))
        parts.append(log.row(row, result, cls=choreo.reveal(done), col=filler_col + FILL_COLS + 1))
        scene.add(group(choreo.reveal(start), parts))

    rows = banner("WHITECCINN")
    banner_w = len(rows[0]) * CHAR_W
    art = Grid(x0=(width - banner_w) / 2, y0=174, line_h=15)
    scene.defs.append(
        f'<linearGradient id="bn" gradientUnits="userSpaceOnUse" x1="{fmt(art.x0)}" y1="0" '
        f'x2="{fmt(art.x0 + banner_w)}" y2="0" spreadMethod="reflect">'
        f'<stop offset="0" stop-color="{PAL["cy"]}"/><stop offset=".5" stop-color="{PAL["vi"]}"/>'
        f'<stop offset="1" stop-color="{PAL["pk"]}"/>'
        f'<animateTransform attributeName="gradientTransform" type="translate" from="0 0" '
        f'to="{fmt(2 * banner_w)} 0" dur="9s" repeatCount="indefinite"/></linearGradient>')
    for i, row in enumerate(rows):
        scene.add(group(choreo.reveal(5.4 + 0.07 * i, fade=0.1), [art.runs(i, shadow_runs(row))]))
    glitch = choreo.flicker([(6.05, 6.12), (6.2, 6.26), (13.4, 13.46), (13.52, 13.58)], peak=0.75)
    scene.add(group(glitch, [
        '<g transform="translate(-3 0)">' + "".join(art.row(i, r, cls="pk") for i, r in enumerate(rows)) + "</g>",
        '<g transform="translate(3 1)">' + "".join(art.row(i, r, cls="cy") for i, r in enumerate(rows)) + "</g>",
    ]))

    tagline: List[Run] = [("dm", "──────  "), ("fg", "full-stack engineer"), ("cy", " · "),
                          ("fg", "distributed systems"), ("cy", " · "), ("fg", "open source"), ("dm", "  ──────")]
    tagline_w = sum(display_width(t) for _, t in tagline) * CHAR_W
    scene.add(group(choreo.reveal(6.2), [Grid(x0=(width - tagline_w) / 2, y0=282).runs(0, tagline)]))

    who = Grid(x0=28, y0=318, line_h=21)
    for i, (key, value) in enumerate(WHOAMI):
        scene.add(group(choreo.reveal(6.7 + 0.3 * i, rise=4),
                        [who.runs(i, [("mu", "> "), ("cy", key.ljust(9))] + list(value))]))
    prompt = len(WHOAMI)
    cursor = (f'<rect x="{fmt(who.x(2))}" y="{fmt(who.y(prompt) - 13)}" width="{fmt(CHAR_W)}" '
              f'height="16" class="cy blink"/>')
    scene.add(group(choreo.reveal(6.7 + 0.3 * prompt + 0.2), [who.runs(prompt, [("gr b", "$")]), cursor]))

    height = who.y(prompt) + 26
    defs, background, overlay = window_chrome(width, height, "ccinn@neural-core: ~ — zsh")
    content = group(choreo.reveal(0, t_out=cycle - 0.9, fade=0.5, persist=True), scene.body)
    return svg_document(
        width, height,
        "whiteCcinn — neural core boot sequence",
        "Animated terminal: a boot log loads weights and storage, a WHITECCINN ASCII banner renders, "
        "then a whoami summary: Wen-hui Cai (ccinn, 白菜), Rust · Go · C/C++ · PHP, distributed storage.",
        base_css() + ".bn{fill:url(#bn)}" + choreo.css(),
        "\n".join([background, content, overlay]),
        defs=defs + "".join(scene.defs))


# --------------------------------------------------------------------------- #
# Brain: neural net forward pass + attention heads + token sampler
# --------------------------------------------------------------------------- #
LAYER_COLS = (8, 21, 34, 47)
LAYER_ROWS = ((1, 5, 9, 13), (1, 4, 7, 10, 13), (1, 4, 7, 10, 13), (4, 10))
IN_LABELS = ("rust", "go", "c/c++", "php")
OUT_LABELS = ("build", "ship")
PULSE_ROUTES = ((0, 1, 3, 0), (2, 4, 1, 1), (1, 2, 2, 0), (3, 0, 4, 1), (0, 3, 0, 1), (2, 2, 3, 0))
NET_FRAMES, NET_PERIOD = 40, 4.0


def rasterize(start: Cell, end: Cell) -> List[Cell]:
    """Bresenham line between two grid cells, endpoints included."""
    (c, r), (c1, r1) = start, end
    dc, dr = abs(c1 - c), -abs(r1 - r)
    sc, sr = (1 if c < c1 else -1), (1 if r < r1 else -1)
    err, cells = dc + dr, []
    while True:
        cells.append((c, r))
        if (c, r) == (c1, r1):
            return cells
        e2 = 2 * err
        if e2 >= dr:
            err, c = err + dr, c + sc
        if e2 <= dc:
            err, r = err + dc, r + sr


@dataclass(frozen=True)
class Network:
    layers: Tuple[Tuple[Cell, ...], ...]   # node centres per layer
    edges: Tuple[Tuple[Cell, ...], ...]    # rasterised cells of every connection
    mesh: frozenset
    paths: Tuple[Tuple[Cell, ...], ...]    # one route per travelling pulse
    frames: int

    @classmethod
    def default(cls) -> "Network":
        layers = tuple(tuple((col, row) for row in rows) for col, rows in zip(LAYER_COLS, LAYER_ROWS))
        wires: Dict[Tuple[Cell, Cell], Tuple[Cell, ...]] = {}
        for left, right in zip(layers, layers[1:]):
            for a in left:
                for b in right:  # wire from the right rim "(o)·" to the left rim "·(o)"
                    wires[(a, b)] = tuple(rasterize((a[0] + 2, a[1]), (b[0] - 2, b[1])))
        paths = []
        for route in PULSE_ROUTES:
            nodes = [layers[depth][index] for depth, index in enumerate(route)]
            path: List[Cell] = [nodes[0]] * 2
            for a, b in zip(nodes, nodes[1:]):
                path += list(wires[(a, b)]) + [b]
            paths.append(tuple(path + [nodes[-1]] * 4))  # dwell so the output visibly fires
        mesh = frozenset(cell for cells in wires.values() for cell in cells)
        return cls(layers, tuple(wires.values()), mesh, tuple(paths), NET_FRAMES)

    def _position(self, pulse: int, frame: int) -> int:
        path = self.paths[pulse]
        phase = pulse / len(self.paths)
        return int((frame / self.frames + phase) * len(path)) % len(path)

    def pulse_heads(self, frame: int) -> List[Cell]:
        return [self.paths[p][self._position(p, frame)] for p in range(len(self.paths))]

    def overlay(self, frame: int) -> Canvas:
        centres = {cell for layer in self.layers for cell in layer}
        canvas = Canvas()
        for p, path in enumerate(self.paths):
            head = self._position(p, frame)
            for back, glyph in ((2, "·"), (1, "•")):
                if head - back >= 0 and path[head - back] not in centres:
                    canvas.put(*path[head - back], glyph, "cy")
        heads = set(self.pulse_heads(frame))
        for col, row in heads:
            if (col, row) in centres:
                canvas.text(col - 1, row, "(●)", "pk")
            else:
                canvas.put(col, row, "●", "cy")
        for label, (col, row) in zip(IN_LABELS, self.layers[0]):
            if (col, row) in heads:
                canvas.text(col - 2 - len(label), row, label, "cy")
        for label, (col, row) in zip(OUT_LABELS, self.layers[-1]):
            if (col, row) in heads:
                canvas.text(col + 3, row, label, "pk b")
        return canvas


TOKENS = ("to", "know", "but", "not", "to", "do", "is", "not", "to", "know")
SHADES = "·░▒▓█"
SHADE_CLASS = {"·": "dm", "░": "h1", "▒": "h2", "▓": "h3", "█": "h4"}
HEAD_PERIOD = 16.0


@dataclass(frozen=True)
class Head:
    name: str
    weights: Tuple[Tuple[float, ...], ...]


HEAD_PATTERNS = (  # (name, score(query i, key j, tokens)) — real circuits seen in transformers
    ("previous token", lambda i, j, t: 3.2 if j == i - 1 else (1.0 if i == j == 0 else 0.0)),
    ("attention sink", lambda i, j, t: 3.0 if j == 0 else (0.8 if j == i else 0.0)),
    ("token match", lambda i, j, t: 3.0 if t[j] == t[i] else 0.0),
    ("induction", lambda i, j, t: 3.2 if 0 < j <= i and t[j - 1] == t[i] else (0.6 if j == i else 0.0)),
    ("diagonal", lambda i, j, t: 3.0 if j == i else 0.0),
    ("broad context", lambda i, j, t: 0.0),
    ("verbs", lambda i, j, t: 2.6 if t[j] in ("know", "do") else 0.0),
    ("negation", lambda i, j, t: 2.6 if t[j] == "not" else 0.0),
)


def attention_heads(seed: int = 42) -> List[Head]:
    """Causal softmax attention maps with light deterministic noise."""
    rng, n, heads = random.Random(seed), len(TOKENS), []
    for name, score in HEAD_PATTERNS:
        rows = []
        for i in range(n):
            logits = [score(i, j, TOKENS) + rng.uniform(-0.35, 0.35) for j in range(i + 1)]
            peak = max(logits)
            exps = [math.exp(v - peak) for v in logits]
            total = sum(exps)
            rows.append(tuple(e / total for e in exps) + (0.0,) * (n - i - 1))
        heads.append(Head(name, tuple(rows)))
    return heads


def shade(weight: float) -> str:
    for glyph, threshold in zip(SHADES, (0.06, 0.15, 0.3, 0.5)):
        if weight < threshold:
            return glyph
    return SHADES[-1]


SENTENCES = (  # (chosen token, runner-up, third) per step
    (("To", "Be", "Build"), (" know", " learn", " ship"), (" but", " is", " and"), (" not", " never", " fail"),
     (" to", " is", " yet"), (" do", " act", " ship"), (" is", " means", " ="), (" not", " to", " only"),
     (" to", " really", " truly"), (" know", " learn", " be"), (".", "!", "…")),
    (("High", "Low", "Zero"), (" QPS", " availability", " throughput"), (",", " and", " at"),
     (" fine", " sharded", " lock"), ("-grained", "-tuned", "print"), (" locks", " shards", " control"),
     (",", " and", "."), (" zero", " no", " few"), (" panics", " GC", " downtime"), (".", "!", " ever")),
    (("Distributed", "Neural", "Modern"), (" systems", " storage", " brains"), (" are", " need", " become"),
     (" neural", " just", " living"), (" nets", " networks", " circuits"), (" that", " with", " which"),
     (" page", " wake", " call"), (" you", " ops", " on-call"), (" at", " around", " by"), (" 3", " 2", " 4"),
     ("am", ":00", " a.m."), (".", "!", " daily")),
)
TOKEN_STEP, TOKEN_LEAD, SENTENCE_HOLD = 0.32, 0.6, 2.2


@dataclass(frozen=True)
class Step:
    token: str
    candidates: Tuple[Tuple[str, float], ...]


@dataclass(frozen=True)
class SampledSentence:
    steps: Tuple[Step, ...]
    eos: float

    @property
    def text(self) -> str:
        return "".join(step.token for step in self.steps)


def display_token(token: str) -> str:
    word = token.strip()
    return word if any(ch.isalnum() for ch in word) else f"'{word or token}'"


def _floor2(value: float) -> float:
    return math.floor(value * 100) / 100


def sampler_plan() -> Tuple[SampledSentence, ...]:
    """Plausible top-3 next-token distributions; floor-rounded so they never sum past 1."""
    plan = []
    for si, sentence in enumerate(SENTENCES):
        steps = []
        for k, (token, *alternatives) in enumerate(sentence):
            rng = random.Random(1000 * si + k)
            top = rng.uniform(0.46, 0.9)
            second = (1 - top) * rng.uniform(0.45, 0.75)
            third = (1 - top - second) * rng.uniform(0.3, 0.7)
            p1, p2 = _floor2(top), _floor2(second)
            p3 = max(0.01, min(_floor2(third), round(p2 - 0.01, 2)))
            words = [display_token(token)] + [display_token(alt) for alt in alternatives]
            steps.append(Step(token, tuple(zip(words, (p1, p2, p3)))))
        plan.append(SampledSentence(tuple(steps), _floor2(random.Random(77 + si).uniform(0.9, 0.99))))
    return tuple(plan)


def sampler_timing(plan: Sequence[SampledSentence]) -> List[Tuple[float, List[float], float]]:
    """(start, token emit times, end) per sentence, laid end to end."""
    timing, clock = [], 0.0
    for sentence in plan:
        times = [clock + TOKEN_LEAD + k * TOKEN_STEP for k in range(len(sentence.steps))]
        end = times[-1] + SENTENCE_HOLD
        timing.append((clock, times, end))
        clock = end
    return timing


def candidate_runs(step: Step) -> List[Run]:
    runs: List[Run] = [("mu", "next › ")]
    for rank, (word, prob) in enumerate(step.candidates):
        hot = rank == 0
        runs += [("cy b" if hot else "mu", word), ("fg" if hot else "dm", f" {prob:.2f} "),
                 ("cy" if hot else "dm", "█" * max(1, round(prob * 8))), ("", "   ")]
    return runs


def brain_svg() -> str:
    width = WIDTH
    net, heads, plan = Network.default(), attention_heads(), sampler_plan()
    timing = sampler_timing(plan)
    choreo, scene = Choreographer(timing[-1][2]), Scene()
    net_strip = FrameStrip("nx", net.frames, NET_PERIOD)
    head_strip = FrameStrip("hd", len(heads), HEAD_PERIOD)

    heading = Grid(x0=24, y0=62)
    att_x0 = 548
    att_col = (att_x0 - heading.x0) / CHAR_W
    scene.add(heading.runs(0, [("cy", "▌ "), ("fg b", "forward pass"), ("mu", "  4→5→5→2 · relu · fp16")]),
              heading.runs(0, [("cy", "▌ "), ("fg b", "attention")], col=att_col),
              f'<path d="M520 50V312" stroke="{PAL["line"]}" stroke-dasharray="2 4"/>')

    grid = Grid(x0=24, y0=92, line_h=15)
    # Hairline wires under the ASCII: the pulses ride Bresenham cells that hug these lines.
    centre = lambda cell: (grid.x(cell[0]) + grid.char_w / 2, grid.y(cell[1]) - 4.5)
    wires = "".join("M{}L{}".format(*(f"{fmt(x)} {fmt(y)}" for x, y in (centre(w[0]), centre(w[-1]))))
                    for w in net.edges)
    scene.add(f'<path d="{wires}" stroke="{PAL["line"]}" stroke-width=".8" fill="none"/>')
    base = Canvas()
    for layer in net.layers:
        for col, row in layer:
            base.text(col - 1, row, "(○)", "mu")
    for label, (col, row) in zip(IN_LABELS, net.layers[0]):
        base.text(col - 2 - len(label), row, label, "mu")
    for label, (col, row) in zip(OUT_LABELS, net.layers[-1]):
        base.text(col + 3, row, label, "mu")
    scene.add(base.render(grid))
    for frame in range(net.frames):
        scene.add(group(net_strip.frame_class(frame), [net.overlay(frame).render(grid)]))

    att = Grid(x0=att_x0, y0=92, line_h=19)
    scene.add(att.row(0, "".join(token[:2].ljust(3) for token in TOKENS), cls="mu", col=5))
    for i, token in enumerate(TOKENS):
        scene.add(att.row(i + 1, token.rjust(4), cls="mu"))
    for k, head in enumerate(heads):
        canvas = Canvas()
        for i, row in enumerate(head.weights):
            for j in range(i + 1):
                glyph = shade(row[j])
                canvas.text(5 + 3 * j, i + 1, glyph * 2, SHADE_CLASS[glyph])
        scene.add(group(head_strip.frame_class(k), [
            heading.runs(0, [("mu", "layer 3 · head "), ("fg", f"{k + 1}/{len(heads)}")], col=att_col + 12),
            canvas.render(att),
            att.runs(11.1, [("mu", "pattern: "), ("fg", head.name)]),
        ]))

    separator: List[Run] = [("dm", "── "), ("cy", "sampler"), ("dm", " ── "), ("mu", "temperature 0.7"),
                            ("dm", " ── "), ("mu", "top_p 0.9"), ("dm", " ── "), ("mu", "seed 42"), ("dm", " ")]
    separator.append(("dm", "─" * (97 - sum(len(t) for _, t in separator))))
    scene.add(Grid(x0=24, y0=338).runs(0, separator))
    cand, out = Grid(x0=24, y0=364), Grid(x0=24, y0=390)
    scene.add(out.runs(0, [("cy b", ">")]))
    for si, (sentence, (start, times, end)) in enumerate(zip(plan, timing)):
        keep = si == 0  # what a reduced-motion viewer sees
        widths, total = [], 0
        for step in sentence.steps:
            total += display_width(step.token)
            widths.append(total)
        motion = choreo.staircase([(start, 0)] + [(t, w * out.char_w) for t, w in zip(times, widths)])
        scene.add(group(choreo.reveal(start, t_out=end - 0.15, fade=0.15, persist=keep), [
            out.row(0, sentence.text, col=2),
            typing_cover(scene, out, 0, 2, total, motion, cursor=True, blink=True),
        ]))
        for t, step in zip(times, sentence.steps):
            scene.add(group(choreo.reveal(t - TOKEN_STEP + 0.02, t_out=t - 0.06, fade=0.04),
                            [cand.runs(0, candidate_runs(step))]))
        eos: List[Run] = [("mu", "next › "), ("pk b", "<|eos|>"), ("fg", f" {sentence.eos:.2f} "),
                          ("pk", "█" * round(sentence.eos * 8))]
        scene.add(group(choreo.reveal(times[-1] + 0.02, t_out=end - 0.15, fade=0.08, persist=keep),
                        [cand.runs(0, eos)]))

    height = out.y(0) + 26
    defs, background, overlay = window_chrome(width, height, "ccinn.forward() — live inference", live=True)
    heat = "".join(f".{cls}{{fill:{color}}}" for cls, color in
                   (("h1", "#4c3d8f"), ("h2", "#7c5cf0"), ("h3", "#c084fc"), ("h4", PAL["pk"])))
    return svg_document(
        width, height,
        "ccinn.forward() — live inference",
        "Animated ASCII dashboard: signals propagate through a 4-5-5-2 neural network from rust, go, c/c++ "
        "and php to build and ship; eight causal attention heads cycle over 'to know but not to do is not to "
        "know'; a sampler generates sentences token by token with top-3 probabilities.",
        base_css() + heat + net_strip.css() + head_strip.css() + choreo.css(),
        "\n".join([background] + scene.body + [overlay]),
        defs=defs + "".join(scene.defs))


# --------------------------------------------------------------------------- #
# Stack: btop-style system monitor instead of logo icons
# --------------------------------------------------------------------------- #
STACK_FRAMES, STACK_PERIOD, STACK_CYCLE = 12, 6.0, 10.0
CORES = (("rust", 86), ("go", 90), ("c", 72), ("c++", 66), ("php", 95), ("python", 58), ("js", 52), ("erlang", 34))


@dataclass(frozen=True)
class Proc:
    pid: int  # easter egg: every PID is the service's default port
    name: str
    running: bool
    cpu: float
    mem: str


PROCS = (
    Proc(9092, "kafka", True, 22.0, "1.2G"), Proc(8081, "flink", True, 17.0, "2.0G"),
    Proc(9870, "hadoop", False, 0.8, "768M"), Proc(9501, "swoole", True, 31.0, "256M"),
    Proc(6379, "redis", True, 9.0, "64M"), Proc(3306, "mysql", True, 12.0, "512M"),
    Proc(5432, "postgres", False, 0.6, "128M"), Proc(27017, "mongodb", False, 1.1, "384M"),
)

LOG = (
    ("0.000000", "kernel", "Linux booted · login shell /bin/bash"),
    ("0.041337", "docker", "12 containers healthy (hadoop · hive · impala · flink)"),
    ("0.064212", "nginx", "reverse proxy up · listening on :443"),
    ("0.087123", "git", "pre-commit hooks armed by husky-php"),
    ("0.102400", "node", "react · electron · express · next.js loaded"),
    ("0.131072", "graphql", "schema stitched · 0 errors"),
    ("0.180000", "ci", "github actions: all checks passed"),
    ("0.262144", "llm", "agent attached · pair-programming mode on"),
    ("0.333333", "erlang", "supervisor tree up · let it crash"),
    ("0.420000", "systemd", "ccinn.service: active (running) since 2015"),
)


def core_loads(frame: int) -> List[int]:
    rng = random.Random(7919 + frame)
    loads = []
    for i, (_, base) in enumerate(CORES):
        wave = 7 * math.sin(2 * math.pi * frame / STACK_FRAMES + i * 1.3)
        loads.append(int(max(2, min(99, round(base + wave + rng.uniform(-3, 3))))))
    return loads


def proc_cpu(frame: int) -> List[float]:
    rng = random.Random(104729 + frame)
    out = []
    for i, proc in enumerate(PROCS):
        if proc.running:
            swing = 1 + 0.35 * math.sin(2 * math.pi * frame / STACK_FRAMES + i * 0.9)
            out.append(round(proc.cpu * swing + rng.uniform(-1.5, 1.5), 1))
        else:
            out.append(round(rng.uniform(0.0, proc.cpu * 2), 1))
    return out


def panel(x: float, y: float, w: float, h: float, title: Sequence[Run]) -> str:
    """Rounded btop-style box with its title sitting on the top border."""
    label_w = sum(display_width(t) for _, t in title) * CHAR_W
    return (f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" rx="7" fill="none" '
            f'stroke="{PAL["line"]}"/><rect x="{fmt(x + 10)}" y="{fmt(y - 9)}" width="{fmt(label_w + 12)}" '
            f'height="18" fill="{PAL["bg"]}"/>' + Grid(x0=x + 16, y0=y + 5).runs(0, title))


def stack_svg() -> str:
    width, height = WIDTH, 420
    choreo, scene = Choreographer(STACK_CYCLE), Scene()
    strip = FrameStrip("st", STACK_FRAMES, STACK_PERIOD)

    scene.add(panel(20, 52, 412, 222, [("cy", "¹"), ("fg b", " cores"), ("mu", " · languages")]),
              panel(448, 52, 412, 222, [("cy", "²"), ("fg b", " procs"), ("mu", " · data & infra · pid≡port")]),
              panel(20, 292, 840, 112, [("cy", "³"), ("fg b", " dmesg"), ("mu", " · tooling")]))

    cores = Grid(x0=36, y0=88, line_h=22)
    for i, (name, _) in enumerate(CORES):
        scene.add(cores.row(i, name, cls="fg"))

    procs = Grid(x0=464, y0=84, line_h=21)
    highlight = choreo.staircase([(i * STACK_CYCLE / len(PROCS), i * procs.line_h) for i in range(len(PROCS))], axis="Y")
    scene.add(f'<g class="{highlight}"><rect x="456" y="{fmt(procs.y(1) - 15)}" width="396" height="20" rx="3" '
              f'fill="{PAL["vi"]}" fill-opacity=".13"/><rect x="456" y="{fmt(procs.y(1) - 15)}" width="2" '
              f'height="20" class="vi"/></g>')
    scene.add(procs.row(0, "PID    NAME      STATE     CPU%    MEM", cls="mu"))
    for i, proc in enumerate(PROCS, start=1):
        state = [("gr", "● "), ("fg", "run ")] if proc.running else [("dm", "○ "), ("mu", "idle")]
        scene.add(procs.runs(i, [("am", f"{proc.pid:<6} "), ("fg", f"{proc.name:<9} ")] + state
                             + [("", " " * 12), ("mu", f"{proc.mem:>5}")]))

    for frame in range(STACK_FRAMES):
        layer = Canvas()
        previous = core_loads((frame - 1) % STACK_FRAMES)
        for i, load in enumerate(core_loads(frame)):
            filled = round(load / 100 * 20)
            col = 8
            for cls, text in meter_runs(filled, 20):
                layer.text(col, i, text, cls)
                col += len(text)
            layer.text(30, i, f"{load:>3}%", "fg")
            layer.text(35, i, "↑" if load >= previous[i] else "↓", "gr" if load >= previous[i] else "pk")
        cpu_rows = Canvas()
        for i, value in enumerate(proc_cpu(frame), start=1):
            cpu_rows.text(26, i, f"{value:>5.1f}", "cy" if PROCS[i - 1].running else "dm")
        scene.add(group(strip.frame_class(frame), [layer.render(cores), cpu_rows.render(procs)]))

    tail = Grid(x0=36, y0=326, line_h=20)
    scene.defs.append('<clipPath id="tail"><rect x="24" y="309" width="832" height="84"/></clipPath>')
    lines = [tail.runs(i, [("dm", f"[{ts:>12}] "), ("cy", f"{source}: "), ("fg", message)])
             for i, (ts, source, message) in enumerate(LOG + LOG)]
    scroll = choreo.staircase([(i * STACK_CYCLE / len(LOG), -i * tail.line_h) for i in range(len(LOG))], axis="Y")
    scene.add(f'<g clip-path="url(#tail)"><g class="{scroll}">' + "\n".join(lines) + "</g></g>")

    defs, background, overlay = window_chrome(width, height, "stackmon — ccinn@neural-core", live=True)
    return svg_document(
        width, height,
        "stackmon — ccinn's tech stack as a system monitor",
        "btop-style ASCII monitor. Cores (languages): rust, go, c, c++, php, python, js, erlang. Processes "
        "(data and infra, PID equals default port): kafka 9092, flink 8081, hadoop 9870, swoole 9501, "
        "redis 6379, mysql 3306, postgres 5432, mongodb 27017. dmesg tail (tooling): linux, bash, docker, "
        "nginx, git, react, electron, express, next.js, graphql, github actions, erlang.",
        base_css() + strip.css() + choreo.css(),
        "\n".join([background] + scene.body + [overlay]),
        defs=defs + "".join(scene.defs))


# --------------------------------------------------------------------------- #
# Repo cards and link buttons
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Repo:
    name: str
    stars: int  # snapshot from the GitHub API; refresh by editing and rebuilding
    forks: int
    lang: str
    color: str
    tags: str
    desc: str


REPOS = (
    Repo("husky-php", 66, 10, "PHP", "#4F5D95", "git-hooks · composer",
         "Git hooks made easy: npm-husky-style hooks for PHP projects, installed through composer."),
    Repo("kafka-swoole", 31, 7, "PHP", "#4F5D95", "kafka · swoole · coroutines",
         "Every Kafka protocol in PHP: high- and low-level clients on Swoole coroutines."),
    Repo("swoole-yaf-better", 25, 5, "PHP", "#4F5D95", "swoole · yaf · coroutines",
         "PHP 7 + Swoole + Yaf with coroutine MySQL/Redis and inotify live log tailing."),
    Repo("IdCenterSender", 15, 5, "PHP", "#4F5D95", "distributed-id · php-ext",
         "Distributed 64-bit ID issuer in PHP, with a native C extension edition."),
    Repo("daemon", 3, 0, "Go", "#00ADD8", "supervisor · process-mgmt",
         "Supervisor-style process daemon for Go, inspired by Erlang and Python supervisors."),
    Repo("flink-sql-submit", 3, 0, "Java", "#b07219", "flink · sql · streaming",
         "A customizable Flink SQL submission client, beyond Flink's default CLI."),
)
CARD_W, CARD_H, CARD_COLS = 430, 136, 46


def wrap(text: str, width: int, max_lines: int) -> List[str]:
    lines: List[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word[:width])
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = (lines[-1][: width - 1].rstrip() + "…") if len(lines[-1]) >= width else lines[-1] + "…"
    return lines


def trace_border(width: float, height: float, delay: float) -> Tuple[str, str]:
    """A short gradient light orbiting the border (normalised with pathLength)."""
    defs = ('<linearGradient id="tr" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{PAL["cy"]}"/><stop offset=".5" stop-color="{PAL["vi"]}"/>'
            f'<stop offset="1" stop-color="{PAL["pk"]}"/></linearGradient>')
    body = (f'<rect x=".5" y=".5" width="{fmt(width - 1)}" height="{fmt(height - 1)}" rx="10" '
            f'fill="{PAL["bg"]}" stroke="{PAL["line"]}"/>'
            f'<rect x=".75" y=".75" width="{fmt(width - 1.5)}" height="{fmt(height - 1.5)}" rx="10" fill="none" '
            f'stroke="url(#tr)" stroke-width="1.5" stroke-linecap="round" pathLength="100" '
            f'stroke-dasharray="12 88" class="trace" style="animation-delay:{fmt(-delay)}s"/>')
    return defs, body


def card_svg(repo: Repo, index: int) -> str:
    defs, frame = trace_border(CARD_W, CARD_H, delay=index * 1.17)
    head = Grid(x0=20, y0=36)
    stats: List[Run] = [("am", "★"), ("fg", f" {repo.stars}"), ("dm", "  "), ("mu", "forks"), ("fg", f" {repo.forks}")]
    stats_w = sum(display_width(t) for _, t in stats)
    cursor_x = head.x(2 + len(repo.name)) + 3
    body = [frame,
            head.runs(0, [("cy", "▌ "), ("fg b", repo.name)]),
            f'<rect x="{fmt(cursor_x)}" y="23" width="7" height="15" class="cy blink"/>',
            head.runs(0, stats, col=CARD_COLS - stats_w)]
    desc = Grid(x0=20, y0=68, line_h=20)
    body += [desc.row(i, line, cls="mu") for i, line in enumerate(wrap(repo.desc, CARD_COLS, 2))]
    body.append(Grid(x0=20, y0=117).runs(0, [("lg", "●"), ("fg", f" {repo.lang}"), ("dm", "  ·  "), ("mu", repo.tags)]))
    return svg_document(CARD_W, CARD_H, f"{repo.name} — {repo.lang}, {repo.stars} stars",
                        repo.desc, base_css() + f".lg{{fill:{repo.color}}}", "\n".join(body), defs=defs)


BUTTONS = (
    ("btn-blog.svg", "Blog: whiteccinn.github.io",
     (("cy", "→ "), ("fg b", "blog"), ("dm", "  ·  "), ("mu", "whiteccinn.github.io"))),
    ("btn-mail.svg", "Email: 471113744@qq.com",
     (("vi", "@ "), ("fg b", "mail"), ("dm", "  ·  "), ("mu", "471113744@qq.com"))),
)
BUTTON_W, BUTTON_H = 300, 44


def button_svg(label: str, runs: Sequence[Run], index: int) -> str:
    defs, frame = trace_border(BUTTON_W, BUTTON_H, delay=index * 2.3)
    text_w = sum(display_width(t) for _, t in runs) * CHAR_W
    content = Grid(x0=(BUTTON_W - text_w) / 2, y0=27).runs(0, runs)
    return svg_document(BUTTON_W, BUTTON_H, label, label, base_css(), frame + content, defs=defs)


# --------------------------------------------------------------------------- #
# Divider: endless token stream
# --------------------------------------------------------------------------- #
STREAM = ("<|bos|>", "0110", "embed", "1011", "attn(Q,K,V)", "0010", "softmax", "1101", "mlp", "0111",
          "residual", "1001", "layernorm", "0100", "logits", "1110", "top_p", "0011", "sample", "1010", "<|eos|>")


def stream_svg() -> str:
    width, height, char_w = WIDTH, 34, 7.8  # 13px font
    accents = round_robin(("cy", "vi", "pk"))
    runs: List[Run] = []
    for token in STREAM:
        runs += [("dm" if set(token) <= {"0", "1"} else next(accents), token), ("mu", " › ")]
    cols = sum(len(t) for _, t in runs)
    loop_w = cols * char_w
    grid = Grid(x0=0, y0=22, char_w=char_w)
    copies = math.ceil(width / loop_w) + 1
    text = "".join(grid.runs(0, runs, col=i * cols) for i in range(copies))
    defs = (f'<clipPath id="pill"><rect width="{width}" height="{height}" rx="8"/></clipPath>'
            '<linearGradient id="fade" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
            '<stop offset=".08" stop-color="#fff"/><stop offset=".92" stop-color="#fff"/>'
            '<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>'
            f'<mask id="edge"><rect width="{width}" height="{height}" fill="url(#fade)"/></mask>')
    css = (base_css() + "text{font-size:13px}"
           f"@keyframes scroll{{to{{transform:translateX(-{fmt(loop_w)}px)}}}}"
           f".scroll{{animation:scroll {fmt(loop_w / 36)}s linear infinite}}")
    body = (f'<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="8" fill="{PAL["bg"]}" '
            f'stroke="{PAL["line"]}"/><g clip-path="url(#pill)" mask="url(#edge)"><g class="scroll">{text}</g></g>')
    return svg_document(width, height, "token stream", "A transformer pipeline scrolling past as tokens.",
                        css, body, defs=defs)


# --------------------------------------------------------------------------- #
def render_all() -> Dict[str, str]:
    assets = {"hero.svg": hero_svg(), "brain.svg": brain_svg(), "stack.svg": stack_svg(), "stream.svg": stream_svg()}
    for index, (name, label, runs) in enumerate(BUTTONS):
        assets[name] = button_svg(label, runs, index)
    for index, repo in enumerate(REPOS):
        assets[f"repo-{repo.name}.svg"] = card_svg(repo, index)
    return assets


def main() -> int:
    out_dir = REPO_ROOT / "assets"
    out_dir.mkdir(exist_ok=True)
    for name, svg in render_all().items():
        (out_dir / name).write_text(svg, encoding="utf-8")
        print(f"{len(svg.encode()) / 1024:6.1f} KB  assets/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
