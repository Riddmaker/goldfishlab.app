"""The geometry of a server-rendered line chart.

The report draws its charts as inline SVG, which needs no charting library, no
JavaScript and no change to the content security policy - the principle every
picture on the site follows (Phase 9). What an SVG needs from the server is
coordinates, and coordinates are arithmetic worth testing, so they are worked
out here rather than with template filters: a line one pixel off in a
template is invisible, and a line drawn from the wrong turn is a wrong answer
that looks right.

Nothing here knows what is being drawn. `simulations/report.py` decides the
series and the scale; this only turns numbers into points.
"""

from dataclasses import dataclass

#: The drawing, in SVG user units. The browser scales it to the column width
#: (`viewBox` + `width: 100%`), so these are proportions, not pixels.
WIDTH = 320
HEIGHT = 160
#: Room for the value labels on the left and the turn numbers underneath.
LEFT = 30
RIGHT = 8
TOP = 8
BOTTOM = 22


@dataclass(frozen=True)
class Tick:
    """One gridline: where it is, and what it says."""

    position: float
    label: str


@dataclass(frozen=True)
class Line:
    """One series, ready for ``<polyline points="...">``."""

    key: str
    label: str
    #: Position in the chart, which picks its colour and its toggle.
    index: int
    values: tuple[float, ...]
    points: str
    #: A ±1 standard deviation band, as ``<polygon points="...">`` - the upper
    #: edge left to right, then the lower edge back. Empty without a spread.
    band: str = ""
    #: What the line counts, in a sentence or two, for the info line under the
    #: chart and the line's ``<title>`` (phase 10 T5.2, F5).
    info: str = ""


@dataclass(frozen=True)
class LineChart:
    """Everything a template needs to draw one chart."""

    lines: tuple[Line, ...]
    x_ticks: tuple[Tick, ...]
    y_ticks: tuple[Tick, ...]
    width: int = WIDTH
    height: int = HEIGHT
    left: int = LEFT
    right: int = WIDTH - RIGHT
    top: int = TOP
    bottom: int = HEIGHT - BOTTOM


def _x(position: int, count: int) -> float:
    """Where the ``position``-th of ``count`` points sits, left to right."""
    span = WIDTH - LEFT - RIGHT
    if count <= 1:
        return LEFT + span / 2
    return LEFT + span * position / (count - 1)


def _y(value: float, top_value: float) -> float:
    """Where ``value`` sits on a scale from 0 at the bottom to ``top_value``.

    Clamped: a value off the scale is a scale chosen wrong, and a line drawn
    through the axis labels would hide that rather than show it.
    """
    span = HEIGHT - TOP - BOTTOM
    share = min(1.0, max(0.0, value / top_value)) if top_value else 0.0
    return TOP + span * (1 - share)


def band(values, spreads, top_value: float) -> str:
    """The polygon of ``value ± spread`` along a line.

    The lower edge stops at zero: a deck cannot draw fewer than no creatures,
    and a band below the axis would say it can. The upper edge is clamped to
    the chart like every other value.
    """
    count = len(values)
    upper = [f"{_x(position, count):.1f},{_y(value + spread, top_value):.1f}"
             for position, (value, spread) in enumerate(zip(values, spreads, strict=True))]
    lower = [f"{_x(position, count):.1f},{_y(max(0.0, value - spread), top_value):.1f}"
             for position, (value, spread) in enumerate(zip(values, spreads, strict=True))]
    return " ".join(upper + lower[::-1])


def line_chart(series, x_labels, *, top_value: float, y_ticks,
               spreads=None, infos=None) -> LineChart:
    """Lay out several series against one shared x axis.

    Args:
        series: ``(key, label, values)`` per line, one value per x label.
        x_labels: What each position on the x axis is called ("1", "2", ...).
        top_value: The value at the top of the chart.
        y_ticks: ``(value, label)`` for each horizontal gridline.
        spreads: ``{key: [standard deviation per position]}`` for a count
            chart; a line without one gets no band.
        infos: ``{key: sentence}``, what each line counts.
    """
    x_labels = tuple(x_labels)
    count = len(x_labels)
    spreads = spreads or {}
    infos = infos or {}
    lines = []
    for index, (key, label, values) in enumerate(series):
        values = tuple(values)
        if len(values) != count:
            raise ValueError(f"{key}: {len(values)} values for {count} positions")
        points = " ".join(
            f"{_x(position, count):.1f},{_y(value, top_value):.1f}"
            for position, value in enumerate(values)
        )
        lines.append(Line(
            key=key, label=label, index=index, values=values, points=points,
            band=band(values, spreads[key], top_value) if key in spreads else "",
            info=infos.get(key, ""),
        ))
    return LineChart(
        lines=tuple(lines),
        x_ticks=tuple(Tick(round(_x(position, count), 1), text)
                      for position, text in enumerate(x_labels)),
        y_ticks=tuple(Tick(round(_y(value, top_value), 1), text)
                      for value, text in y_ticks),
    )


def count_scale(largest: float) -> tuple[float, list[tuple[float, str]]]:
    """A scale for counts: the top at the next whole number, a tick per step.

    At most five gridlines, so that a deck that sees twenty lands by turn ten
    gets ticks every five rather than a ladder of twenty.
    """
    top = max(1, int(largest) + (0 if float(largest).is_integer() else 1))
    step = max(1, -(-top // 4))
    top = step * -(-top // step)
    return float(top), [(value, str(value)) for value in range(0, top + 1, step)]


#: The scale every share chart uses. Always the whole 0-100, so that two
#: charts side by side cannot make a small difference look like a big one.
PERCENT_TOP = 100.0
PERCENT_TICKS = [(value, f"{value}%") for value in (0, 25, 50, 75, 100)]
