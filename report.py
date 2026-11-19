"""
"Explain my catchment": a short Word report for one site, written in plain English,
with a map, charts and tables, ready to paste into a thesis or project report.
"""

import io
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

import analysis as A
import imd_tools as T

BLUE, RED, ORANGE, GREY, INK = "#2a78d6", "#e34948", "#eb6834", "#8a8985", "#1f2933"
RAIN = T.VARIABLES["rain"]["column"]
TMAX = T.VARIABLES["tmax"]["column"]
TMIN = T.VARIABLES["tmin"]["column"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#9aa1a9", "axes.labelcolor": INK, "xtick.color": INK,
                     "ytick.color": INK, "axes.titleweight": "bold", "axes.titlesize": 10})


def _fig_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _p(p):
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def _num(v, digits=0):
    return f"{v:,.{digits}f}"


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def map_figure(res, site):
    s = next(x for x in res.sites if x.name == site)
    var = "rain" if (site, "rain") in res.cell_means else next(v for (n, v) in res.cell_means if n == site)
    cm = res.cell_means[(site, var)]
    vcol = cm.columns[-1]
    h = T.VARIABLES[var]["grid"].step / 2
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    vals = cm[vcol].where(cm["Has data"])
    vmin, vmax = np.nanmin(vals) if vals.notna().any() else 0, np.nanmax(vals) if vals.notna().any() else 1
    cmap = plt.get_cmap("Blues" if var == "rain" else "Oranges")
    for la, lo, v in zip(cm["Latitude"], cm["Longitude"], vals):
        level = 0.2 + 0.75 * (v - vmin) / ((vmax - vmin) or 1) if not np.isnan(v) else 0
        colour = "#e3e3e0" if np.isnan(v) else cmap(level)
        ax.add_patch(plt.Rectangle((lo - h, la - h), 2 * h, 2 * h, facecolor=colour, edgecolor="white", lw=1))
        if not np.isnan(v) and len(cm) <= 40:
            ax.text(lo, la, _num(v), ha="center", va="center", fontsize=7,
                    color="white" if level > 0.6 else INK)
    if s.is_point:
        ax.plot(s.geometry.x, s.geometry.y, "o", color=ORANGE, mec="white", ms=8)
    else:
        geoms = getattr(s.geometry, "geoms", [s.geometry])
        for g in geoms:
            if hasattr(g, "exterior"):
                x, y = g.exterior.xy
                ax.plot(x, y, color=INK, lw=1.6)
    ax.set_aspect(1 / np.cos(np.radians(s.centre[0])))
    ax.autoscale_view()
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_title(f"IMD grid cells used, coloured by {vcol[0].lower() + vcol[1:]}")
    return _fig_bytes(fig)


def yearly_figure(yt, col, slope, intercept):
    d = yt[["Year", col]].dropna()
    fig, ax = plt.subplots(figsize=(6.4, 2.8))
    ax.bar(d.Year, d[col], color=BLUE, width=0.8)
    ax.axhline(d[col].mean(), color=GREY, ls="--", lw=1.2, label=f"Average {_num(d[col].mean())} mm")
    if slope is not None and not np.isnan(slope):
        xs = np.arange(len(yt))
        ax.plot(yt.Year, intercept + slope * xs, color=INK, ls=":", lw=1.5, label="Trend (Sen's slope)")
    ax.set_ylabel(col)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.02), ncol=2, fontsize=8)
    ax.set_title("Rainfall year by year", pad=22)
    return _fig_bytes(fig)


def monthly_figure(mt, col):
    clim = mt.groupby("Month")[col].mean()
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.bar([MONTHS[m - 1] for m in clim.index], clim.values, color=BLUE)
    ax.set_ylabel(f"Average {col[0].lower() + col[1:]}")
    ax.set_title("The average year, month by month")
    return _fig_bytes(fig)


def return_level_figure(am, d=1):
    col = f"Largest {d}-day rainfall (mm)"
    x = am[col].values
    obs, Tobs = A.plotting_positions(x)
    Ts = np.logspace(np.log10(1.05), 2, 80)
    xi, a = A.fit_gumbel(x)
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.plot(Ts, [A.gumbel_quantile(xi, a, t) for t in Ts], color=BLUE, lw=2, label="Gumbel")
    try:
        g = A.fit_gev(x)
        ax.plot(Ts, [A.gev_quantile(*g, t) for t in Ts], color=ORANGE, lw=2, ls="--", label="GEV")
    except Exception:
        pass
    ax.plot(Tobs, obs, "o", color=INK, ms=4, label="Observed yearly largest")
    ax.set_xscale("log")
    ax.set_xticks(A.RETURN_PERIODS)
    ax.set_xticklabels([str(t) for t in A.RETURN_PERIODS])
    ax.set_xlabel("Return period (years)")
    ax.set_ylabel(f"{d}-day rainfall (mm)")
    ax.set_title(f"How big a {d}-day rainfall to expect, and how often")
    ax.legend(frameon=False, fontsize=8)
    return _fig_bytes(fig)


def spi_figure(spi_t, scale=12):
    col = f"SPI {scale}-month"
    d = spi_t.dropna(subset=[col])
    dates = pd.to_datetime(dict(year=d.Year, month=d.Month, day=1))
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.bar(dates, d[col], width=25, color=np.where(d[col] >= 0, BLUE, RED))
    for yv in (-1, 1):
        ax.axhline(yv, color=GREY, lw=0.8, ls=":")
    ax.axhline(0, color=GREY, lw=0.8)
    ax.set_ylabel(f"{scale}-month SPI")
    ax.set_title(f"Wet (blue) and dry (red) periods, {scale}-month Standardised Precipitation Index")
    return _fig_bytes(fig)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _table(doc, df, widths=None, font=8):
    t = doc.add_table(rows=1, cols=len(df.columns))
    t.style = "Light Grid Accent 1"
    for i, c in enumerate(df.columns):
        cell = t.rows[0].cells[i]
        cell.text = str(c)
    for _, r in df.iterrows():
        cells = t.add_row().cells
        for i, v in enumerate(r.values):
            cells[i].text = "" if (isinstance(v, float) and np.isnan(v)) else (
                _num(v, 1) if isinstance(v, float) else str(v))
    for row in t.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(font)
    return t


def build_report(res, tables, site):
    """Make the Word report for one site and return its bytes."""
    s = next(x for x in res.sites if x.name == site)
    df = res.daily[site]
    yt = T.yearly_table(df)
    mt = T.monthly_table(df)
    tr = tables.get("Trends", pd.DataFrame())
    tr = tr[tr.Site == site] if len(tr) else tr
    period = f"{res.start_date:%d/%m/%Y} to {res.end_date:%d/%m/%Y}"

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = Cm(2.2)
        sec.top_margin = sec.bottom_margin = Cm(2)

    doc.add_heading(f"Rainfall and climate of {site}", level=0)
    p = doc.add_paragraph()
    p.add_run(f"Period: {period}.  Made on {date.today():%d/%m/%Y} with IMD Data Puller "
              "(doi.org/10.5281/zenodo.24352511), from India Meteorological Department gridded data.").italic = True

    # 1. The place
    doc.add_heading("1. The place", level=1)
    lat, lon = s.centre
    cells = res.cells[(res.cells.Site == site)] if res.cells is not None and len(res.cells) else pd.DataFrame()
    rain_cells = cells[cells.Variable == "Rainfall"] if len(cells) else cells
    if s.is_point:
        dist = rain_cells["Distance from point (km)"].iloc[0] if len(rain_cells) else None
        doc.add_paragraph(
            f"{site} is a point at {lat:.4f}° N, {lon:.4f}° E. Its values come from the IMD grid cell "
            f"nearest to it" + (f", whose centre is {dist:.1f} km away." if dist is not None else "."))
    else:
        area = T.area_km2(s.geometry)
        n_ok = int(rain_cells["Has data"].sum()) if len(rain_cells) else 0
        cover = 100 * rain_cells.loc[rain_cells["Has data"], "Weight"].sum() if len(rain_cells) else 0
        doc.add_paragraph(
            f"{site} covers about {_num(area)} km², centred near {lat:.3f}° N, {lon:.3f}° E. "
            f"Its rainfall is the area-weighted average of {n_ok} IMD grid cells of 0.25° (about 25 km), "
            f"each counted in proportion to how much of the area it covers. "
            f"{cover:.0f}% of the area is covered by cells with data.")
    if res.cell_means:
        doc.add_picture(map_figure(res, site), width=Cm(11))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 2. Rainfall
    if RAIN in df:
        doc.add_heading("2. Rainfall at a glance", level=1)
        full = yt.dropna(subset=[RAIN])
        r = df[RAIN]
        if len(full):
            mean = full[RAIN].mean()
            wet = full.loc[full[RAIN].idxmax()]
            dry = full.loc[full[RAIN].idxmin()]
            mons = "Monsoon rainfall, June to September (mm)"
            share = 100 * full[mons].sum() / full[RAIN].sum()
            ri = T.rain_indices(df)
            rainy = ri["Rainy days (2.5 mm or more)"].mean() if len(ri) else np.nan
            text = (f"Over the {len(full)} complete years, {site} received on average {_num(mean)} mm of rain "
                    f"a year, of which {share:.0f}% fell in the monsoon months of June to September. "
                    f"The wettest year was {int(wet.Year)} ({_num(wet[RAIN])} mm) and the driest was "
                    f"{int(dry.Year)} ({_num(dry[RAIN])} mm). ")
            if not np.isnan(rainy):
                text += f"There were about {rainy:.0f} rainy days (2.5 mm or more) in a typical year. "
            text += (f"The wettest single day was {r.idxmax():%d %B %Y}, with {_num(r.max())} mm.")
            doc.add_paragraph(text)
            row = tr[tr.Measure == RAIN] if len(tr) else pd.DataFrame()
            slope = intercept = None
            if len(row):
                r0 = row.iloc[0]
                slope = r0["Change per decade"] / 10
                xs = np.arange(len(yt))
                ok = yt[RAIN].notna().values
                intercept = np.median(yt[RAIN].values[ok] - slope * xs[ok])
                clear = r0["Clear trend? (p below 0.05)"]
                if clear.startswith("Yes"):
                    doc.add_paragraph(
                        f"Yearly rainfall shows a clear {'rise' if slope > 0 else 'fall'} of about "
                        f"{_num(abs(r0['Change per decade']))} mm per decade "
                        f"({abs(r0['Change per decade (% of average)']):.1f}% of the average; Mann-Kendall "
                        f"{_p(r0['p-value'])}).")
                else:
                    doc.add_paragraph(
                        f"There is no clear trend in yearly rainfall over this period (Sen's slope "
                        f"{r0['Change per decade']:+.0f} mm per decade, Mann-Kendall {_p(r0['p-value'])}; "
                        "a p-value below 0.05 would be needed to call it clear).")
            doc.add_picture(yearly_figure(yt, RAIN, slope, intercept), width=Cm(15.5))
            doc.add_picture(monthly_figure(mt, RAIN), width=Cm(15.5))
        else:
            doc.add_paragraph(
                f"Between {period}, {site} received {_num(r.sum())} mm of rain in total, with "
                f"{int((r >= T.RAINY_DAY_MM).sum())} rainy days (2.5 mm or more). The wettest day was "
                f"{r.idxmax():%d %B %Y}, with {_num(r.max())} mm. The period does not cover a whole year, "
                "so yearly averages, trends, design rainfall and the drought index are not included.")

    # 3. Temperature and evaporation
    if TMAX in df or TMIN in df:
        doc.add_heading("3. Temperature and evaporation", level=1)
        parts = []
        if TMAX in df:
            mx = df[TMAX].groupby(df.index.month).mean()
            parts.append(f"The average daily maximum temperature is {df[TMAX].mean():.1f} °C; the hottest "
                         f"month is usually {MONTHS[int(mx.idxmax()) - 1]} ({mx.max():.1f} °C).")
        if TMIN in df:
            mn = df[TMIN].groupby(df.index.month).mean()
            parts.append(f"The average daily minimum is {df[TMIN].mean():.1f} °C, coolest in "
                         f"{MONTHS[int(mn.idxmin()) - 1]} ({mn.min():.1f} °C).")
        if T.PET_COLUMN in df and len(yt.dropna(subset=[T.PET_COLUMN])):
            pet = yt[T.PET_COLUMN].mean()
            parts.append(f"Potential evaporation (Hargreaves method) is about {_num(pet)} mm a year.")
            if RAIN in df and yt[RAIN].mean() > 0:
                di = pet / yt[RAIN].mean()
                parts.append(f"The dryness index (evaporation ÷ rainfall) is {di:.2f}, so the place is "
                             f"{'water-limited (drier)' if di > 1 else 'energy-limited (wetter)'}.")
        parts.append("IMD's temperature grid is coarse (1°, about 110 km), so these are regional values.")
        doc.add_paragraph(" ".join(parts))

    # 4. Design rainfall
    if RAIN in df:
        doc.add_heading("4. Heavy rainfall for design", level=1)
        am = A.annual_maxima(df)
        dr, notes = A.design_rainfall(df)
        if dr.empty:
            doc.add_paragraph(" ".join(notes))
        else:
            g1 = dr[(dr.Duration == "1 day") & (dr.Method == "Gumbel")].iloc[0]
            g3 = dr[(dr.Duration == "3 days") & (dr.Method == "Gumbel")].iloc[0]
            doc.add_paragraph(
                f"From the largest rainfall of each of {len(am)} years, a 1-day rainfall of about "
                f"{_num(g1['25-year (mm)'])} mm can be expected on average once in 25 years, and about "
                f"{_num(g1['100-year (mm)'])} mm once in 100 years (Gumbel curve). Over 3 days in a row, the "
                f"25-year value is about {_num(g3['25-year (mm)'])} mm. 'Once in 25 years' means a 4% chance "
                "in any one year, not that it happens exactly every 25 years.")
            doc.add_picture(return_level_figure(am, 1), width=Cm(15.5))
            _table(doc, dr)
            for n in notes:
                doc.add_paragraph(n, style="List Bullet")

    # 5. Droughts
    if RAIN in df:
        doc.add_heading("5. Droughts and wet periods", level=1)
        sp, notes = A.spi(df)
        if sp.empty:
            doc.add_paragraph(" ".join(notes))
        else:
            ev = A.drought_periods(sp, 3)
            doc.add_paragraph(
                "The Standardised Precipitation Index (SPI) shows how unusually wet or dry each period was "
                "compared with the years pulled: 0 is normal, −1 or less is dry, +1 or more is wet. "
                "The 3-month index follows seasonal droughts; the 12-month index follows longer dry spells.")
            if len(ev):
                worst = ev.iloc[0]
                doc.add_paragraph(
                    f"There were {len(ev)} drought periods (3-month SPI of −1 or less). The most severe ran "
                    f"from {worst['Start']} to {worst['End']} ({int(worst['Length (months)'])} months), reaching "
                    f"{worst['Lowest SPI']:.2f} ({worst['Class at its worst'].lower()}) in {worst['Driest month']}.")
            doc.add_picture(spi_figure(sp, 12), width=Cm(15.5))
            if len(ev):
                _table(doc, ev.head(5))
            for n in notes:
                doc.add_paragraph(n, style="List Bullet")

    # 6. Cautions
    others = [x.name for x in res.sites if x.name != site]
    cautions = [n for n in res.notes + res.problems if not any(f"'{o}'" in n for o in others)]
    if cautions:
        doc.add_heading("6. Things to keep in mind", level=1)
        for n in cautions:
            doc.add_paragraph(n, style="List Bullet")

    # 7. Data and methods
    doc.add_heading("Data and methods", level=1)
    for line in [
        "Rainfall: IMD 0.25° daily gridded rainfall (Pai et al., 2014, Mausam 65(1), 1–18).",
        "Temperature: IMD 1° daily gridded temperature (Srivastava et al., 2009, Atmospheric Science "
        "Letters 10, 249–254).",
        "Area averages are weighted by the share of the area in each grid cell. Months and years with any "
        "missing day are left out of totals.",
        "Trends: Sen's slope and the Mann-Kendall test. Design rainfall: Gumbel and GEV curves fitted by "
        "L-moments. Drought index: SPI with a gamma curve fitted to each calendar month.",
        "Tool: Madival, H. P. (2026). IMD Data Puller (Version 1.0.0). Zenodo. "
        "https://doi.org/10.5281/zenodo.24352511",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
