"""Inline SVG line charts for the local web page. Pure drawing; no file or network access."""
from datetime import datetime
from html import escape

WIDTH, HEIGHT = 480, 200
LEFT, RIGHT, TOP, BOTTOM = 84, 12, 12, 30


def _moment(captured_at):
    return datetime.fromisoformat(captured_at)


def _summary(title, first, last, format_value):
    change = last - first
    if not change:
        movement = 'no change'
    else:
        movement = ('+' if change > 0 else '−') + format_value(abs(change))
    return f'{title}: {format_value(first)} → {format_value(last)} ({movement})'


def line_chart_svg(title, points, format_value):
    """Return a <figure> charting oldest-first (captured_at, value) points against real time."""
    heading = f'<figcaption class="chart-title">{escape(title)}</figcaption>'
    if len(points) < 2:
        return f'<figure class="chart">{heading}<p class="note">Needs two full snapshots with this value.</p></figure>'
    moments = [_moment(captured_at) for captured_at, _ in points]
    values = [value for _, value in points]
    start, span = moments[0], (moments[-1] - moments[0]).total_seconds()
    low, high = min(values), max(values)
    plot_width, plot_height = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def x(moment):
        if not span:
            return LEFT + plot_width / 2
        return LEFT + (moment - start).total_seconds() / span * plot_width

    def y(value):
        if high == low:
            return TOP + plot_height / 2
        return TOP + (high - value) / (high - low) * plot_height

    coordinates = [(round(x(moment), 2), round(y(value), 2)) for moment, value in zip(moments, values)]
    dots = ''.join(f'<circle class="chart-dot" cx="{cx}" cy="{cy}" r="3.5">'
                   f'<title>{escape(moment.strftime("%Y-%m-%d %H:%M UTC"))}: {escape(format_value(value))}</title></circle>'
                   for (cx, cy), moment, value in zip(coordinates, moments, values))
    line = ' '.join(f'{cx},{cy}' for cx, cy in coordinates)
    summary = _summary(title, values[0], values[-1], format_value)
    bottom = HEIGHT - BOTTOM
    labels = (f'<text class="chart-label" x="{LEFT - 6}" y="{TOP + 4}" text-anchor="end">{escape(format_value(high))}</text>'
              f'<text class="chart-label" x="{LEFT - 6}" y="{bottom}" text-anchor="end">{escape(format_value(low))}</text>'
              f'<text class="chart-label" x="{LEFT}" y="{HEIGHT - 8}">{escape(moments[0].strftime("%Y-%m-%d"))}</text>'
              f'<text class="chart-label" x="{WIDTH - RIGHT}" y="{HEIGHT - 8}" text-anchor="end">{escape(moments[-1].strftime("%Y-%m-%d"))}</text>')
    svg = (f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(summary)}">'
           f'<line class="chart-axis" x1="{LEFT}" y1="{TOP}" x2="{LEFT}" y2="{bottom}"/>'
           f'<line class="chart-axis" x1="{LEFT}" y1="{bottom}" x2="{WIDTH - RIGHT}" y2="{bottom}"/>'
           f'{labels}<polyline class="chart-line" points="{line}"/>{dots}</svg>')
    return f'<figure class="chart">{heading}{svg}<p class="chart-summary">{escape(summary)}</p></figure>'
