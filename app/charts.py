"""Plotly figure builders for the dashboard.

Conventions (one system for every chart):
  * colour identifies the brand and follows the brand, never its rank: a filter does
    not repaint the brands that remain;
  * a solid line is a price this tracker read from Best Buy itself, a dashed line is
    backfill from a third-party price log;
  * prices hold until they change, so lines are step-shaped; a day nobody observed
    is a gap, not a guess.
"""
from __future__ import annotations

import html

import pandas as pd
import plotly.graph_objects as go

# Categorical palette, fixed order; validated for colour-vision deficiency in both modes.
LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"]
NEUTRAL = "#898781"
# The eight makers seen most often keep one colour everywhere. Eight hues cannot all be
# told apart pairwise, so the assignment puts the closest hues on the brands that least
# often share a comparison group (measured over the tracked groups with the palette
# validator), and charts never rely on colour alone: every brand line is also labelled
# at its end, listed in the legend and named in the hover.
BRAND_SLOT = {"Lenovo": 0, "Microsoft": 1, "Acer": 2, "Dell": 3, "HP": 4, "Samsung": 5, "LG": 6, "ASUS": 7}

DASH = "5px,4px"


def brand_colors(brands: list[str], dark: bool) -> dict[str, str]:
    """Colour per brand for one comparison group. Pass the group's full brand list,
    not the filtered one, so a brand keeps its colour when others are hidden."""
    palette = DARK if dark else LIGHT
    free = [i for i in range(len(palette)) if i not in {BRAND_SLOT[b] for b in brands if b in BRAND_SLOT}]
    out = {}
    for brand in sorted(brands):
        if brand in BRAND_SLOT:
            out[brand] = palette[BRAND_SLOT[brand]]
        else:   # a rarer maker takes a slot nobody in this group uses; past eight, neutral
            out[brand] = palette[free.pop(0)] if free else NEUTRAL
    return out


def source_colors(dark: bool) -> dict[str, str]:
    palette = DARK if dark else LIGHT
    return {"Own scraper (Best Buy)": palette[0], "Honey": palette[1], "ShopSavvy": palette[2]}


def money(v: float) -> str:
    return f"${v:,.2f}"


def _layout(fig: go.Figure, *, hovermode: str, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=4, r=12, t=36, b=4), hovermode=hovermode,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None,
                    itemclick="toggle", itemdoubleclick="toggleothers"),
        # type is set explicitly: the legend-only traces carry no dates to infer it from
        xaxis=dict(type="date", showgrid=False, hoverformat="%a %d %b %Y", title=None),
        yaxis=dict(tickprefix="$", tickformat=",.0f", zeroline=False, gridwidth=1, title=None),
        hoverlabel=dict(namelength=-1, align="left"),
    )
    return fig


def _end_labels(fig: go.Figure, series: list[dict], height: int) -> None:
    """Name and latest price at the right end of each line, so a line is never
    identified by colour alone. Labels that would overlap are moved apart."""
    ends = [(s["group"], s["color"], float(s["y"].dropna().iloc[-1])) for s in series if s["y"].notna().any()]
    values = pd.concat([s["y"] for s in series]).dropna()
    if not ends or values.empty:
        return
    low, high = float(values.min()), float(values.max())
    pad = (high - low) * 0.07 or max(high * 0.05, 1.0)
    low, high = low - pad, high + pad
    fig.update_yaxes(range=[low, high])
    per_px = (high - low) / max(height - 76, 1)       # plot height: figure minus margins and tick labels
    gap = 16 * per_px
    spots = sorted(([value, value, group, color] for group, color, value in ends), key=lambda e: e[0])
    for _ in range(50):                               # push overlapping labels apart, keeping their order
        moved = False
        for below, above in zip(spots, spots[1:]):
            overlap = gap - (above[0] - below[0])
            if overlap > 1e-9:
                below[0] -= overlap / 2
                above[0] += overlap / 2
                moved = True
        if not moved:
            break
    for spot, value, group, color in spots:
        fig.add_annotation(xref="paper", x=1, xanchor="left", xshift=8, y=spot, yanchor="middle", showarrow=False,
                           align="left", font=dict(size=12),
                           text=f"<span style='color:{color}'>●</span> {html.escape(group, quote=False)} "
                                f"<b>{money(value)}</b>")
    fig.update_layout(margin=dict(r=168))


def trend_figure(series: list[dict], *, dark: bool, hovermode: str = "x unified", height: int = 430,
                 boundary: pd.Timestamp | None = None, end_labels: bool = False) -> go.Figure:
    """Step lines over time.

    Each item of `series`:
        group   legend entry (and the unit a legend click shows or hides)
        color   line colour
        y       price per calendar day (NaN = no observation)
        own     True on days read first-hand, False on backfilled days
        hover   text per day
    `boundary` marks the first day of first-hand prices. `end_labels` writes each
    group's name and latest price at the right edge (one series per group only).
    """
    fig = go.Figure()
    surface = "#0e1117" if dark else "#ffffff"
    in_legend: set[str] = set()
    for s in series:
        y = s["y"].astype(float)
        own = s["own"].reindex(y.index).eq(True) & y.notna()
        back = y.notna() & ~own
        color, group = s["color"], s["group"]
        if group not in in_legend:      # one legend entry per brand, always drawn solid
            in_legend.add(group)
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name=group, legendgroup=group,
                                     line=dict(color=color, width=2), hoverinfo="skip"))
        shared = dict(x=y.index, legendgroup=group, showlegend=False, connectgaps=False,
                      customdata=s["hover"], hovertemplate="%{customdata}<extra></extra>")
        if back.any():
            fig.add_trace(go.Scatter(y=y.where(back), mode="lines",
                                     line=dict(color=color, width=2, dash=DASH, shape="hv"), **shared))
        if own.any():
            few = int(own.sum()) <= 21      # a short first-hand series would be invisible as a line
            fig.add_trace(go.Scatter(y=y.where(own), mode="lines+markers" if few else "lines",
                                     line=dict(color=color, width=2, shape="hv"),
                                     marker=dict(size=8, color=color, line=dict(width=2, color=surface)),
                                     **shared))
        # Join the two styles where one hands over to the other on consecutive days.
        before = y.shift(1)
        handovers = ((own & back.shift(1, fill_value=False), DASH),    # backfill held until the first own reading
                     (back & own.shift(1, fill_value=False), None))    # own reading held until a backfilled day
        for mask, dash in handovers:
            if mask.any():
                xs, ys = [], []
                for day in y.index[mask]:
                    xs += [day - pd.Timedelta(days=1), day, None]
                    ys += [before[day], y[day], None]
                fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", hoverinfo="skip", legendgroup=group,
                                         showlegend=False,
                                         line=dict(color=color, width=2, dash=dash, shape="hv")))
    if boundary is not None:
        fig.add_shape(type="line", x0=boundary, x1=boundary, yref="paper", y0=0, y1=1,
                      line=dict(color="#383835" if dark else "#c3c2b7", width=1))
        fig.add_annotation(x=boundary, yref="paper", y=0, yanchor="bottom", xanchor="right", xshift=-6,
                           showarrow=False, text=f"own scraper from {boundary:%d %b %Y}",
                           font=dict(size=11, color=NEUTRAL))
    _layout(fig, hovermode=hovermode, height=height)
    days = [s["y"].index for s in series if len(s["y"])]
    if days:        # end the axis at the last observed day instead of the next round month
        fig.update_xaxes(range=[min(d.min() for d in days) - pd.Timedelta(hours=18),
                                max(d.max() for d in days) + pd.Timedelta(hours=30)])
    if end_labels:
        _end_labels(fig, series, height)
    return fig


def sources_figure(lines: list[dict], *, dark: bool, height: int = 360) -> go.Figure:
    """One laptop, one step line per collection method (name, color, y, markers)."""
    fig = go.Figure()
    surface = "#0e1117" if dark else "#ffffff"
    for line in lines:
        y = line["y"].astype(float)
        marks = line["markers"].reindex(y.index).eq(True) & y.notna()
        hover = [f"<b>{html.escape(line['name'], quote=False)}</b>  {money(v)}" if pd.notna(v) else "" for v in y]
        fig.add_trace(go.Scatter(x=y.index, y=y, mode="lines", name=line["name"], legendgroup=line["name"],
                                 line=dict(color=line["color"], width=2, shape="hv"), connectgaps=False,
                                 customdata=hover, hovertemplate="%{customdata}<extra></extra>"))
        if marks.any():
            fig.add_trace(go.Scatter(x=y.index[marks], y=y[marks], mode="markers", legendgroup=line["name"],
                                     showlegend=False, hoverinfo="skip",
                                     marker=dict(size=8, color=line["color"], line=dict(width=2, color=surface))))
    return _layout(fig, hovermode="x unified", height=height)
