"""
Charts for the IMD Data Puller app (Altair).

Colours follow one rule each:
  - one series      -> blue
  - several sites   -> a fixed order of colour-blind-safe hues
  - above/below average -> blue and red around zero
  - magnitude (heatmap, cell map) -> light to dark blue (orange for temperature)
"""

import altair as alt
import numpy as np
import pandas as pd

import imd_tools as T

BLUE = "#2a78d6"
RED = "#e34948"
GREY = "#8a8985"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
BLUE_RAMP = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
ORANGE_RAMP = ["#fde3d4", "#f6b08f", "#eb6834", "#b8461b", "#7a2c0f"]

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def is_total(col):
    return T._how(col) == "total"


def is_temperature(col):
    return "temperature" in col.lower()


def _unit(col):
    return col[col.rfind("(") + 1:-1] if "(" in col else ""


def daily(df, col):
    """Daily line, zoom with the mouse wheel and drag, hover for values."""
    d = df[[col]].reset_index().rename(columns={col: "Value"})
    zoom = alt.selection_interval(bind="scales", encodings=["x"])
    hover = alt.selection_point(fields=["Date"], nearest=True, on="pointerover", empty=False)
    base = alt.Chart(d).encode(x=alt.X("Date:T", title=None))
    line = base.mark_line(strokeWidth=1.5, color=BLUE).encode(
        y=alt.Y("Value:Q", title=col)).add_params(zoom)
    rule = base.mark_rule(color=GREY).encode(
        opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("Date:T", format="%d %b %Y"), alt.Tooltip("Value:Q", title=col, format=".2f")],
    ).add_params(hover)
    point = base.mark_point(filled=True, size=60, color=BLUE).encode(
        y="Value:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    return (line + rule + point).properties(height=320)


def yearly(yt, col):
    """Yearly values with the long-term average and the Sen's slope trend line."""
    d = yt[["Year", col]].dropna().rename(columns={col: "Value"})
    if d.empty:
        return None
    mean = d.Value.mean()
    slope, p = T.mann_kendall(yt[col].values)
    x = alt.X("Year:O", title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True))
    tip = [alt.Tooltip("Year:O"), alt.Tooltip("Value:Q", title=col, format=".1f")]
    if is_total(col):
        marks = alt.Chart(d).mark_bar(color=BLUE, cornerRadiusEnd=3).encode(x=x, y=alt.Y("Value:Q", title=col),
                                                                            tooltip=tip)
    else:
        marks = alt.Chart(d).mark_line(color=BLUE, strokeWidth=2, point=alt.OverlayMarkDef(size=40)).encode(
            x=x, y=alt.Y("Value:Q", title=col, scale=alt.Scale(zero=False)), tooltip=tip)
    layers = [marks]
    ref = pd.DataFrame({"Value": [mean], "Label": [f"Average {mean:.1f}"]})
    layers.append(alt.Chart(ref).mark_rule(color=GREY, strokeDash=[4, 4], strokeWidth=1.5).encode(y="Value:Q"))
    if not np.isnan(slope):
        xs = np.arange(len(yt))
        ok = yt[col].notna().values
        intercept = np.median(yt[col].values[ok] - slope * xs[ok])
        tl = pd.DataFrame({"Year": yt.Year.values, "Trend": intercept + slope * xs})
        layers.append(alt.Chart(tl).mark_line(color=SERIES[1], strokeWidth=2, strokeDash=[2, 3]).encode(
            x=x, y="Trend:Q",
            tooltip=[alt.Tooltip("Year:O"), alt.Tooltip("Trend:Q", format=".1f", title="Trend line")]))
    return alt.layer(*layers).properties(height=320), mean, slope, p


def anomaly(yt, col):
    """How far each year is from the long-term average (percent for totals, degrees for temperature)."""
    d = yt[["Year", col]].dropna().copy()
    if d.empty:
        return None
    mean = d[col].mean()
    if is_total(col) and mean:
        d["Difference"] = 100 * (d[col] - mean) / mean
        title, fmt = "Difference from average (%)", ".1f"
    else:
        d["Difference"] = d[col] - mean
        title, fmt = f"Difference from average ({_unit(col)})", ".2f"
    if is_temperature(col):
        d["Compared with average"] = np.where(d.Difference >= 0, "Warmer", "Cooler")
        domain, colours = ["Warmer", "Cooler"], [RED, BLUE]
    else:
        word_up = "Wetter" if col == T.VARIABLES["rain"]["column"] else "Higher"
        word_dn = "Drier" if col == T.VARIABLES["rain"]["column"] else "Lower"
        d["Compared with average"] = np.where(d.Difference >= 0, word_up, word_dn)
        domain, colours = [word_up, word_dn], [BLUE, RED]
    bars = alt.Chart(d).mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("Year:O", title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        y=alt.Y("Difference:Q", title=title),
        color=alt.Color("Compared with average:N", scale=alt.Scale(domain=domain, range=colours),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Year:O"), alt.Tooltip(f"{col}:Q", format=".1f"),
                 alt.Tooltip("Difference:Q", title=title, format=fmt)])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=GREY).encode(y="y:Q")
    return (bars + zero).properties(height=300)


def heatmap(mt, col):
    """Every month of every year as a coloured square."""
    d = mt[["Year", "Month", col]].copy()
    d["Month name"] = [MONTHS[m - 1] for m in d.Month]
    ramp = ORANGE_RAMP if is_temperature(col) else BLUE_RAMP
    return alt.Chart(d).mark_rect(stroke="#80808040", strokeWidth=1).encode(
        x=alt.X("Year:O", title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        y=alt.Y("Month name:O", sort=MONTHS, title=None),
        color=alt.Color(f"{col}:Q", scale=alt.Scale(range=ramp), title=col,
                        legend=alt.Legend(orient="right", gradientLength=200)),
        tooltip=[alt.Tooltip("Year:O"), alt.Tooltip("Month name:N", title="Month"),
                 alt.Tooltip(f"{col}:Q", format=".1f")],
    ).properties(height=300)


def average_year(mt, col):
    """The average of each calendar month."""
    d = mt.groupby("Month")[col].mean().reset_index()
    d["Month name"] = [MONTHS[m - 1] for m in d.Month]
    return alt.Chart(d).mark_bar(color=BLUE, cornerRadiusEnd=3).encode(
        x=alt.X("Month name:O", sort=MONTHS, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y(f"{col}:Q", title=f"Average {col[0].lower() + col[1:]}"),
        tooltip=[alt.Tooltip("Month name:N", title="Month"), alt.Tooltip(f"{col}:Q", format=".1f")],
    ).properties(height=300)


def compare_sites(yearly_all, col, sites):
    """Yearly values of several sites on one chart (first 8 sites)."""
    d = yearly_all[yearly_all.Site.isin(sites)][["Site", "Year", col]].dropna()
    return alt.Chart(d).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=36)).encode(
        x=alt.X("Year:O", title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        y=alt.Y(f"{col}:Q", scale=alt.Scale(zero=is_total(col))),
        color=alt.Color("Site:N", scale=alt.Scale(domain=list(sites), range=SERIES[:len(sites)]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Site:N"), alt.Tooltip("Year:O"), alt.Tooltip(f"{col}:Q", format=".1f")],
    ).properties(height=340)


def index_bars(ri, col):
    d = ri[["Year", col]].dropna()
    return alt.Chart(d).mark_bar(color=BLUE, cornerRadiusEnd=3).encode(
        x=alt.X("Year:O", title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        y=alt.Y(f"{col}:Q"),
        tooltip=[alt.Tooltip("Year:O"), alt.Tooltip(f"{col}:Q", format=".1f")],
    ).properties(height=300)


def return_levels(am, duration=1):
    """Observed yearly largest rainfall against fitted Gumbel and GEV curves (log x-axis)."""
    import analysis as A
    col = f"Largest {duration}-day rainfall (mm)"
    x = am[col].values
    obs, t_obs = A.plotting_positions(x)
    pts = pd.DataFrame({"Return period (years)": t_obs, "Rainfall (mm)": obs, "Series": "Observed yearly largest"})
    ts = np.logspace(np.log10(1.05), 2, 60)
    xi, a = A.fit_gumbel(x)
    lines = [pd.DataFrame({"Return period (years)": ts,
                           "Rainfall (mm)": [A.gumbel_quantile(xi, a, t) for t in ts], "Series": "Gumbel"})]
    try:
        g = A.fit_gev(x)
        lines.append(pd.DataFrame({"Return period (years)": ts,
                                   "Rainfall (mm)": [A.gev_quantile(*g, t) for t in ts], "Series": "GEV"}))
    except Exception:
        pass
    lines = pd.concat(lines)
    domain = ["Gumbel", "GEV", "Observed yearly largest"]
    colour = alt.Color("Series:N", scale=alt.Scale(domain=domain, range=[BLUE, SERIES[1], GREY]),
                       legend=alt.Legend(orient="top", title=None))
    x_enc = alt.X("Return period (years):Q", scale=alt.Scale(type="log", domain=[1, 100]),
                  axis=alt.Axis(values=[1, 2, 5, 10, 25, 50, 100], format="d"))
    curves = alt.Chart(lines).mark_line(strokeWidth=2).encode(
        x=x_enc, y=alt.Y("Rainfall (mm):Q", title=f"{duration}-day rainfall (mm)", scale=alt.Scale(zero=False)),
        color=colour, strokeDash=alt.condition(alt.datum.Series == "GEV", alt.value([6, 3]), alt.value([1, 0])),
        tooltip=[alt.Tooltip("Series:N"), alt.Tooltip("Return period (years):Q", format=".0f"),
                 alt.Tooltip("Rainfall (mm):Q", format=".1f")])
    dots = alt.Chart(pts).mark_point(filled=True, size=50).encode(
        x=x_enc, y="Rainfall (mm):Q", color=colour,
        tooltip=[alt.Tooltip("Return period (years):Q", format=".1f", title="Observed return period"),
                 alt.Tooltip("Rainfall (mm):Q", format=".1f")])
    return (curves + dots).properties(height=330)


def spi_bars(spi_table, scale=3):
    """SPI month by month: blue above zero (wetter), red below (drier)."""
    col = f"SPI {scale}-month"
    d = spi_table.dropna(subset=[col]).copy()
    d["Month start"] = pd.to_datetime(dict(year=d.Year, month=d.Month, day=1))
    d["Compared with normal"] = np.where(d[col] >= 0, "Wetter", "Drier")
    bars = alt.Chart(d).mark_bar().encode(
        x=alt.X("Month start:T", title=None),
        y=alt.Y(f"{col}:Q", title=f"{scale}-month SPI"),
        color=alt.Color("Compared with normal:N", scale=alt.Scale(domain=["Wetter", "Drier"], range=[BLUE, RED]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Month start:T", format="%b %Y", title="Month"), alt.Tooltip(f"{col}:Q", format=".2f"),
                 alt.Tooltip(f"{col} class:N", title="Class")])
    rules = alt.Chart(pd.DataFrame({"y": [-2, -1.5, -1, 1, 1.5, 2]})).mark_rule(
        color=GREY, strokeDash=[2, 3], strokeWidth=0.8).encode(y="y:Q")
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=GREY).encode(y="y:Q")
    return (bars + rules + zero).properties(height=300)
