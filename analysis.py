"""
Extra hydrology analysis for the IMD Data Puller.

1. Design rainfall: the largest 1, 2, 3 and 5-day rainfall each year, and the
   rainfall expected once in 2, 5, 10, 25, 50 and 100 years (return periods),
   from two standard curves fitted by L-moments: Gumbel and GEV.
2. Drought index: the Standardised Precipitation Index (SPI) at 1, 3, 6 and
   12 months, and a list of drought periods.

Everything here works on one site's daily table (a pandas DataFrame with a
'Rainfall (mm)' column and dates as the index).
"""

import math

import numpy as np
import pandas as pd

RAIN = "Rainfall (mm)"
DURATIONS = [1, 2, 3, 5]
RETURN_PERIODS = [2, 5, 10, 25, 50, 100]
SPI_SCALES = [1, 3, 6, 12]
EULER = 0.5772156649


# ---------------------------------------------------------------------------
# 1. Design rainfall
# ---------------------------------------------------------------------------

def _days_in_year(y):
    return 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365


def annual_maxima(df):
    """Largest 1, 2, 3 and 5-day rainfall totals in each complete year.

    A '2-day' total is the rain of two days in a row added together, and so on.
    Years with any missing day, or only partly inside the chosen dates, are left out.
    """
    if RAIN not in df:
        return pd.DataFrame()
    s = df[RAIN]
    rows = []
    for year, part in s.groupby(s.index.year):
        if len(part) < _days_in_year(year) or part.isna().any():
            continue
        row = {"Year": int(year)}
        for d in DURATIONS:
            roll = part.rolling(d).sum()
            row[f"Largest {d}-day rainfall (mm)"] = round(float(roll.max()), 1)
            label = (f"Date of largest {d}-day rainfall" if d == 1
                     else f"Last day of largest {d}-day rainfall")
            row[label] = roll.idxmax().strftime("%d/%m/%Y")
        rows.append(row)
    return pd.DataFrame(rows)


def l_moments(x):
    """First three sample L-moments (l1, l2, t3) using probability-weighted moments."""
    x = np.sort(np.asarray(x, float))
    n = len(x)
    j = np.arange(1, n + 1)
    b0 = x.mean()
    b1 = np.sum((j - 1) / (n - 1) * x) / n
    b2 = np.sum((j - 1) * (j - 2) / ((n - 1) * (n - 2)) * x) / n
    l1 = b0
    l2 = 2 * b1 - b0
    l3 = 6 * b2 - 6 * b1 + b0
    return l1, l2, l3 / l2


def fit_gumbel(x):
    """Gumbel (EV1) parameters by L-moments: location xi, scale alpha."""
    l1, l2, _ = l_moments(x)
    alpha = l2 / math.log(2)
    return l1 - EULER * alpha, alpha


def gumbel_quantile(xi, alpha, T):
    return xi - alpha * math.log(-math.log(1 - 1 / T))


def fit_gev(x):
    """GEV parameters by L-moments (Hosking, 1985): location xi, scale alpha, shape k.
    k > 0 means a bounded upper tail, k < 0 a heavy upper tail (Hosking's sign)."""
    l1, l2, t3 = l_moments(x)
    c = 2 / (3 + t3) - math.log(2) / math.log(3)
    k = 7.8590 * c + 2.9554 * c ** 2
    g = math.gamma(1 + k)
    alpha = l2 * k / ((1 - 2 ** (-k)) * g)
    xi = l1 - alpha * (1 - g) / k
    return xi, alpha, k


def gev_quantile(xi, alpha, k, T):
    y = -math.log(1 - 1 / T)
    return xi + alpha / k * (1 - y ** k)


def design_rainfall(df, min_years=10):
    """Return levels for each duration and return period, from both curves.

    Returns (table, notes). The table has one row per duration and method.
    """
    am = annual_maxima(df)
    notes = []
    if len(am) < min_years:
        return pd.DataFrame(), [f"Design rainfall needs at least {min_years} complete years "
                                f"(this site has {len(am)})."]
    if len(am) < 30:
        notes.append(f"Only {len(am)} complete years: the 50 and 100-year values are rough. "
                     "About 30 or more years are recommended.")
    rows = []
    for d in DURATIONS:
        x = am[f"Largest {d}-day rainfall (mm)"].values
        xi, a = fit_gumbel(x)
        rows.append({"Duration": f"{d} day" + ("s" if d > 1 else ""), "Method": "Gumbel",
                     **{f"{T}-year (mm)": round(gumbel_quantile(xi, a, T), 1) for T in RETURN_PERIODS}})
        try:
            gxi, ga, gk = fit_gev(x)
            rows.append({"Duration": f"{d} day" + ("s" if d > 1 else ""), "Method": "GEV",
                         **{f"{T}-year (mm)": round(gev_quantile(gxi, ga, gk, T), 1) for T in RETURN_PERIODS}})
        except (ValueError, ZeroDivisionError, OverflowError):
            notes.append(f"The GEV curve could not be fitted for {d}-day rainfall.")
    notes.append("IMD's days run from 8:30 am to 8:30 am, so these are fixed-day totals. For the "
                 "largest 24 hours at any start time, engineers often multiply the 1-day values by 1.13 (WMO).")
    return pd.DataFrame(rows), notes


def plotting_positions(x):
    """Observed return periods for the sorted annual maxima (Gringorten formula)."""
    x = np.sort(np.asarray(x, float))[::-1]
    n = len(x)
    rank = np.arange(1, n + 1)
    p = (rank - 0.44) / (n + 0.12)          # chance of being exceeded in a year
    return x, 1 / p


# ---------------------------------------------------------------------------
# 2. Standardised Precipitation Index (SPI)
# ---------------------------------------------------------------------------

SPI_CLASSES = [
    (-np.inf, -2.0, "Extremely dry"),
    (-2.0, -1.5, "Severely dry"),
    (-1.5, -1.0, "Moderately dry"),
    (-1.0, 1.0, "Near normal"),
    (1.0, 1.5, "Moderately wet"),
    (1.5, 2.0, "Very wet"),
    (2.0, np.inf, "Extremely wet"),
]


def spi_class(v):
    if v is None or np.isnan(v):
        return ""
    for lo, hi, name in SPI_CLASSES:
        if lo < v <= hi or (lo == -np.inf and v <= hi):
            return name
    return ""


def _monthly_rain(df):
    s = df[RAIN]
    g = s.resample("MS")
    total = g.sum()
    count = g.count()
    full = pd.Series(total.index.days_in_month, index=total.index)
    return total.where(count == full)


def spi(df, scales=SPI_SCALES, min_years=10):
    """SPI at each time scale, month by month.

    How it works: for a 3-month SPI, add up the rain of each month and the two
    before it. For each calendar month (all Januaries, all Februaries, ...), fit a
    gamma curve to those totals, allowing for totals of zero. Then turn each total
    into 'how many standard deviations from normal' it is. 0 = normal,
    -1 or less = dry, +1 or more = wet.
    Returns (table, notes).
    """
    from scipy import stats
    if RAIN not in df:
        return pd.DataFrame(), ["The drought index needs rainfall."]
    m = _monthly_rain(df)
    n_years = m.dropna().size / 12
    notes = []
    if n_years < min_years:
        return pd.DataFrame(), [f"The drought index needs at least {min_years} years of complete months "
                                f"(this site has about {n_years:.0f})."]
    if n_years < 30:
        notes.append(f"About {n_years:.0f} years of data: the index is more reliable with 30 or more years.")
    out = pd.DataFrame({"Year": m.index.year, "Month": m.index.month,
                        "Monthly rainfall (mm)": m.round(1).values}, index=m.index)
    for k in scales:
        acc = m.rolling(k).sum()               # empty if any month in the window is missing
        z = pd.Series(np.nan, index=m.index)
        for month in range(1, 13):
            sel = acc[acc.index.month == month].dropna()
            if len(sel) < min_years:
                continue
            zeros = (sel <= 0).sum()
            q = zeros / len(sel)
            pos = sel[sel > 0].values
            if len(pos) < 5 or np.allclose(pos, pos[0]):
                continue
            a, loc, scale = stats.gamma.fit(pos, floc=0)
            cdf = q + (1 - q) * stats.gamma.cdf(sel.values, a, loc=0, scale=scale)
            cdf = np.clip(cdf, 1e-6, 1 - 1e-6)
            z.loc[sel.index] = stats.norm.ppf(cdf)
        out[f"SPI {k}-month"] = z.round(2).values
        out[f"SPI {k}-month class"] = [spi_class(v) for v in z.values]
    notes.append("The index compares each period with the years you pulled, so a longer period "
                 "(30 years or more) gives a fairer 'normal'.")
    return out.reset_index(drop=True), notes


def drought_periods(spi_table, scale=3, threshold=-1.0):
    """Runs of months with SPI at or below the threshold (default: 3-month SPI of -1 or less)."""
    col = f"SPI {scale}-month"
    if spi_table.empty or col not in spi_table:
        return pd.DataFrame()
    rows, run = [], []
    for _, r in spi_table.iterrows():
        v = r[col]
        if not np.isnan(v) and v <= threshold:
            run.append(r)
        else:
            if run:
                rows.append(run)
            run = []
    if run:
        rows.append(run)
    events = []
    for run in rows:
        vals = [r[col] for r in run]
        worst = run[int(np.argmin(vals))]
        events.append({
            "Start": f"{int(run[0]['Month']):02d}/{int(run[0]['Year'])}",
            "End": f"{int(run[-1]['Month']):02d}/{int(run[-1]['Year'])}",
            "Length (months)": len(run),
            "Lowest SPI": round(float(min(vals)), 2),
            "Driest month": f"{int(worst['Month']):02d}/{int(worst['Year'])}",
            "Severity (sum of SPI)": round(float(sum(vals)), 2),
            "Class at its worst": spi_class(min(vals)),
        })
    return pd.DataFrame(events).sort_values("Severity (sum of SPI)").reset_index(drop=True) if events \
        else pd.DataFrame()
