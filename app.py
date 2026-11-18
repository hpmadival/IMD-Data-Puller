"""
IMD Data Puller: a small web app to get IMD gridded rainfall and temperature
for a point, a box, or catchment boundaries, and download it.

Run it with:   streamlit run app.py
"""

import datetime
import os

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import Point, box, shape
from streamlit_folium import st_folium

import charts as C
import imd_tools as T

st.set_page_config(page_title="IMD Data Puller", page_icon="🌧️", layout="wide")

THIS_YEAR = datetime.date.today().year
NAME_OF = {v: T.VARIABLES[v]["name"] for v in T.VARIABLES}
ss = st.session_state
ss.setdefault("points", pd.DataFrame({"Name": ["Point 1"], "Latitude": [12.30], "Longitude": [75.60]}))
ss.setdefault("points_version", 0)
ss.setdefault("box", {"Name": "My area", "North": 12.60, "South": 12.00, "West": 75.00, "East": 75.80})
ss.setdefault("box_version", 0)
ss.setdefault("result", None)


# ---------------------------------------------------------------------------
# Small cached helpers (so a click doesn't redo slow work)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def saved_counts(data_dir, backup_dir):
    return {v: T.saved_count(data_dir, v, backup_dir) for v in T.VARIABLES}


@st.cache_data(ttl=60, show_spinner=False)
def years_to_download(need, start, end, data_dir, backup_dir):
    return sum(1 for v in need for y in range(start, end + 1)
               if y >= T.VARIABLES[v]["first_year"] and not T.is_available(data_dir, v, y, backup_dir))


@st.cache_data(show_spinner=False, max_entries=500)
def preview_cells(wkb, var):
    from shapely import wkb as W
    return T.cells_for_site(T.Site("x", W.loads(wkb)), var)


@st.cache_data(show_spinner=False, max_entries=500)
def area_of(wkb):
    from shapely import wkb as W
    return T.area_km2(W.loads(wkb))


def cells_geojson(sites, var):
    """All preview cells as one GeoJSON layer (one layer draws far faster than many squares)."""
    h = T.VARIABLES[var]["grid"].step / 2
    feats = []
    for s in sites:
        cells = preview_cells(s.geometry.wkb, var)
        for la, lo, fr in zip(cells["Latitude"], cells["Longitude"], cells["Fraction of cell inside"]):
            frac = 1.0 if s.is_point else float(fr)
            tip = f"Cell {la:.2f} N, {lo:.2f} E" + ("" if s.is_point else f" | {frac * 100:.0f}% inside")
            feats.append({"type": "Feature",
                          "properties": {"tip": tip, "op": round(0.1 + 0.4 * min(frac, 1.0), 3)},
                          "geometry": {"type": "Polygon", "coordinates": [[
                              [lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]]}})
    return {"type": "FeatureCollection", "features": feats}


# ---------------------------------------------------------------------------
# Sidebar: what, when, extras
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("1. What data")
    variables = st.multiselect("Variables", list(T.VARIABLES), default=["rain"],
                               format_func=NAME_OF.get)
    add_pet = st.checkbox(
        "Add potential evaporation (Hargreaves)",
        help="An estimate of how much water the air could evaporate each day, worked out "
             "from maximum and minimum temperature. Both temperatures are pulled automatically.")

    st.header("2. Which years")
    chosen = set(variables) | ({"tmax", "tmin"} if add_pet else set())
    earliest = min([T.VARIABLES[v]["first_year"] for v in chosen] or [1901])
    c1, c2 = st.columns(2)
    start_year = c1.number_input("From", min_value=1901, max_value=THIS_YEAR,
                                 value=max(1991, earliest), step=1)
    end_year = c2.number_input("To", min_value=1901, max_value=THIS_YEAR, value=2020, step=1)
    st.caption("Rainfall starts in 1901, temperature in 1951. IMD usually publishes "
               "a year's files a few months after it ends.")

    st.header("3. Extra outputs")
    keep_cells = st.checkbox("Daily values of every grid cell (CSV)",
                             help="The values of each grid cell before averaging, one column per cell.")
    keep_grid = st.checkbox("Cut-out grid (NetCDF)",
                            help="The grid around your sites, for use in GIS or Python.")

    st.header("4. Options")
    shift_label = st.selectbox(
        "Rainfall dates", list(T.DATE_SHIFT_TEXT.values()), index=0,
        help="IMD's rain day runs from 8:30 am to 8:30 am. If your streamflow or other records "
             "file the same day under a different date, move rainfall by one day so the two line up.")
    rain_shift = {v: k for k, v in T.DATE_SHIFT_TEXT.items()}[shift_label]
    add_dsr = st.checkbox("Add a 'Days since rain' column",
                          help="Counts the days since the last rainy day. Handy for picking dry-weather "
                               "periods, such as recession periods.")
    dsr_mm = st.number_input("A day counts as rainy from (mm)", min_value=0.1, value=1.0, step=0.5,
                             disabled=not add_dsr)

    st.header("Data folder")
    data_dir = st.text_input(
        "Downloaded IMD files are kept here",
        value=os.environ.get("IMD_DATA_DIR", "imd-data"),
        help="Each year is downloaded once and reused next time. Use a folder on a fast local "
             "disk; in Colab the notebook sets this up for you.")
    backup_dir = os.environ.get("IMD_BACKUP_DIR") or None
    if backup_dir:
        st.caption(f"Backup copies go to: {backup_dir}")
    saved = saved_counts(data_dir, backup_dir)
    st.caption("Years already saved: " + ", ".join(f"{NAME_OF[v].lower()} {n}" for v, n in saved.items()))
    if T.backups_pending():
        st.caption(f"Copying {T.backups_pending()} new file(s) to the backup folder in the background.")


# ---------------------------------------------------------------------------
# Map helper
# ---------------------------------------------------------------------------
def make_map(sites, grid_var, draw=False):
    m = folium.Map(location=[20, 78], zoom_start=5, control_scale=True)
    folium.TileLayer("Esri.WorldImagery", name="Satellite", show=False).add_to(m)
    total_cells = 0
    for s in sites:
        if s.is_point:
            folium.Marker([s.geometry.y, s.geometry.x], tooltip=s.name).add_to(m)
        else:
            folium.GeoJson(s.geometry.__geo_interface__, tooltip=s.name,
                           style_function=lambda _: {"color": "#d62728", "weight": 2,
                                                     "fillOpacity": 0.05}).add_to(m)
    # grid cells that will be used (preview: assumes every cell has data), as one layer
    gj = cells_geojson(sites, grid_var)
    total_cells = len(gj["features"])
    if 0 < total_cells <= 20000:
        folium.GeoJson(gj, name="Grid cells",
                       style_function=lambda f: {"color": "#1f77b4", "weight": 1,
                                                 "fillColor": "#1f77b4", "fillOpacity": f["properties"]["op"]},
                       tooltip=folium.GeoJsonTooltip(fields=["tip"], labels=False)).add_to(m)
    if draw:
        Draw(draw_options={"rectangle": True, "polyline": False, "polygon": False,
                           "circle": False, "marker": False, "circlemarker": False},
             edit_options={"edit": False, "remove": False}).add_to(m)
    if sites:
        minx, miny, maxx, maxy = np.array([s.geometry.bounds for s in sites]).T
        pad = 0.3
        m.fit_bounds([[miny.min() - pad, minx.min() - pad], [maxy.max() + pad, maxx.max() + pad]])
    folium.LayerControl().add_to(m)
    return m, total_cells


# ---------------------------------------------------------------------------
# Main page: where
# ---------------------------------------------------------------------------
st.title("🌧️ IMD Data Puller")
st.write("Get IMD's daily gridded rainfall and temperature for points, a box, or your own "
         "catchment boundaries, then download it as ready-to-use files.")

st.subheader("Where?")
mode = st.radio("Choose how to pick the place", ["Points", "A box", "Upload boundary file"],
                horizontal=True, label_visibility="collapsed")
grid_choice = st.radio("Show grid cells on the map for",
                       ["Rainfall (0.25° cells)", "Temperature (1° cells)"], horizontal=True)
grid_var = "rain" if grid_choice.startswith("Rain") else "tmax"

sites = []
left, right = st.columns([1, 2])

if mode == "Points":
    with left:
        with st.expander("Load many points from a CSV file"):
            st.caption("The file needs a column for latitude and one for longitude, "
                       "and ideally one for names.")
            pts_file = st.file_uploader("Points file", type=["csv", "txt"], label_visibility="collapsed")
            if pts_file is not None and ss.get("pts_file_key") != (pts_file.name, pts_file.size):
                ss.pts_file_key = (pts_file.name, pts_file.size)
                try:
                    table = pd.read_csv(pts_file)
                    ss.points = T.points_from_table(table)
                    ss.points_version += 1
                    st.rerun()
                except Exception as err:
                    st.error(f"Could not read the points: {err}")
        st.write("Type points in the table, or click the map to add one.")
        edited = st.data_editor(
            ss.points, key=f"points_{ss.points_version}", num_rows="dynamic", width="stretch",
            column_config={"Latitude": st.column_config.NumberColumn(format="%.4f", min_value=-90, max_value=90),
                           "Longitude": st.column_config.NumberColumn(format="%.4f", min_value=-180, max_value=180)})
        used = set()
        for i, r in edited.reset_index(drop=True).iterrows():
            if pd.notna(r.get("Latitude")) and pd.notna(r.get("Longitude")):
                name = r.get("Name") if isinstance(r.get("Name"), str) and r.get("Name").strip() else f"Point {i + 1}"
                sites.append(T.Site(T.clean_name(name, used), Point(float(r.Longitude), float(r.Latitude))))
    with right:
        m, _ = make_map(sites, grid_var)
        out = st_folium(m, key="map_points", height=480, use_container_width=True,
                        returned_objects=["last_clicked"])
        click = (out or {}).get("last_clicked")
        if click and (click["lat"], click["lng"]) != ss.get("seen_click"):
            ss.seen_click = (click["lat"], click["lng"])
            new = pd.DataFrame({"Name": [f"Point {len(edited) + 1}"],
                                "Latitude": [round(click["lat"], 4)], "Longitude": [round(click["lng"], 4)]})
            ss.points = pd.concat([edited, new], ignore_index=True)
            ss.points_version += 1
            st.rerun()

elif mode == "A box":
    with left:
        st.write("Type the edges of the box, or draw a rectangle on the map "
                 "(the square button on the map's left side).")
        v = ss.box_version
        name = st.text_input("Name", ss.box["Name"], key=f"bn{v}")
        a, b = st.columns(2)
        north = a.number_input("North edge (latitude)", value=ss.box["North"], format="%.4f", key=f"bN{v}")
        south = b.number_input("South edge (latitude)", value=ss.box["South"], format="%.4f", key=f"bS{v}")
        west = a.number_input("West edge (longitude)", value=ss.box["West"], format="%.4f", key=f"bW{v}")
        east = b.number_input("East edge (longitude)", value=ss.box["East"], format="%.4f", key=f"bE{v}")
        if north > south and east > west:
            sites = [T.Site(name or "My area", box(west, south, east, north))]
        else:
            st.error("North must be bigger than south, and east bigger than west.")
    with right:
        m, _ = make_map(sites, grid_var, draw=True)
        out = st_folium(m, key="map_box", height=480, use_container_width=True,
                        returned_objects=["last_active_drawing"])
        drawing = (out or {}).get("last_active_drawing")
        if drawing and str(drawing) != ss.get("seen_drawing"):
            ss.seen_drawing = str(drawing)
            w, s_, e, n = shape(drawing["geometry"]).bounds
            ss.box = {"Name": name, "North": round(n, 4), "South": round(s_, 4),
                      "West": round(w, 4), "East": round(e, 4)}
            ss.box_version += 1
            st.rerun()

else:
    with left:
        files = st.file_uploader(
            "Zipped shapefile, or the .shp .shx .dbf .prj parts together, or GeoJSON / GeoPackage / KML",
            accept_multiple_files=True,
            type=["zip", "shp", "shx", "dbf", "prj", "cpg", "geojson", "json", "gpkg", "kml"])
        if files:
            key = tuple((f.name, f.size) for f in files)
            if ss.get("upload_key") != key:
                try:
                    gdf, warns = T.read_boundary_files([(f.name, f.getvalue()) for f in files])
                    ss.upload = (gdf, warns, None)
                except Exception as err:
                    ss.upload = (None, [], str(err))
                ss.upload_key = key
            gdf, warns, error = ss.upload
            for w in warns:
                st.warning(w)
            if error:
                st.error(error)
            if gdf is not None:
                cols = [c for c in gdf.columns if c != "geometry"]
                guess = T.guess_name_column(gdf)
                name_col = st.selectbox("Which column holds the names?", cols or ["(none)"],
                                        index=cols.index(guess) if guess in cols else 0)
                sites = T.sites_from_frame(gdf, name_col if cols else None)
                st.caption(f"{len(sites)} shape(s) found.")
                st.dataframe(pd.DataFrame({
                    "Name": [s.name for s in sites],
                    "Type": ["Point" if s.is_point else "Area" for s in sites],
                    "Area (km²)": [round(area_of(s.geometry.wkb), 1) for s in sites]}),
                    hide_index=True, width="stretch", height=min(300, 40 + 35 * len(sites)))
    with right:
        m, n_cells = make_map(sites, grid_var)
        st_folium(m, key="map_upload", height=480, use_container_width=True, returned_objects=[])
        if n_cells > 20000:
            st.caption("Too many grid cells to draw on the map; they will still be used.")

st.caption("Blue squares are the IMD grid cells that will be used. Darker means more of the cell "
           "is inside your area. For points, the nearest cell with data is used.")




# ---------------------------------------------------------------------------
# Get the data
# ---------------------------------------------------------------------------
st.subheader("Get the data")
problems = []
if not sites:
    problems.append("Add at least one point or area.")
if not variables and not add_pet:
    problems.append("Pick at least one variable in the sidebar.")
if start_year > end_year:
    problems.append("The 'From' year is after the 'To' year.")
if add_dsr and "rain" not in variables:
    problems.append("'Days since rain' needs Rainfall to be one of the variables.")
outside = T.check_inside_india(sites) if sites else []
if outside:
    problems.append("These are outside IMD's India grid: " + ", ".join(outside))

if not problems:
    need = tuple(sorted(set(variables) | ({"tmax", "tmin"} if add_pet else set())))
    to_get = years_to_download(need, int(start_year), int(end_year), data_dir, backup_dir)
    if to_get:
        st.info(f"{to_get} yearly file(s) need downloading from IMD first "
                f"(rainfall files are about 25 MB each, three are fetched at a time). "
                f"This can take a while; they are saved for next time.")
for p in problems:
    st.warning(p)

if st.button("Get the data", type="primary", disabled=bool(problems)):
    bar = st.progress(0.0, text="Starting")
    try:
        res = T.pull_data(sites, variables, int(start_year), int(end_year), data_dir,
                          add_pet=add_pet, keep_cell_values=keep_cells, keep_grid=keep_grid,
                          rain_shift=rain_shift, days_since_rain_mm=dsr_mm if add_dsr else None,
                          backup_dir=backup_dir,
                          progress=lambda f, msg: bar.progress(min(max(f, 0.0), 1.0), text=msg))
        res.settings["How the place was chosen"] = mode
        bar.progress(1.0, text="Working out summaries and packing the files")
        tables = T.all_tables(res)
        ss.result = {"res": res, "tables": tables, "zip": T.build_zip(res, tables), "excel": None}
        saved_counts.clear(); years_to_download.clear()
        bar.empty()
    except Exception as err:
        bar.empty()
        st.error(f"Something went wrong: {err}")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def show_table(df, **kw):
    st.dataframe(df, hide_index=True, width="stretch", **kw)


# The results area is one "fragment": changing a choice inside it redraws only the
# results, not the whole page (the map and sidebar are left alone).

def downloads_box(res, tables):
    period = f"{res.start_year}-{res.end_year}"
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Everything (zip of CSV files)", ss.result["zip"], file_name=f"IMD data {period}.zip",
                       mime="application/zip", type="primary", width="stretch")
    slot = d2.empty()
    if ss.result["excel"] is None:
        if slot.button("Make the Excel workbook", width="stretch",
                       help="All the tables in one Excel file, plus a daily sheet for each site (up to 25)."):
            with st.spinner("Making the Excel workbook"):
                ss.result["excel"] = T.build_excel(res, tables)
    if ss.result["excel"] is not None:
        slot.download_button("⬇️ Excel workbook", ss.result["excel"], file_name=f"IMD data {period}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           width="stretch")


def overview_tab(res, tables, site_names):
    st.write("**Summary by site**")
    show_table(tables["Summary by site"])
    a, b = st.columns([2, 1])
    site = a.selectbox("Daily data for", site_names, key="ov_site")
    b.download_button(f"⬇️ Daily data for {site} (CSV)",
                      res.daily[site].to_csv(date_format="%Y-%m-%d").encode("utf-8-sig"),
                      file_name=f"{T.safe_file_name(site)} daily.csv", mime="text/csv", width="stretch")
    show_table(res.daily[site].reset_index(), height=280)


def charts_tab(res, site_names):
    a, b = st.columns(2)
    site = a.selectbox("Site", site_names, key="ch_site")
    df = res.daily[site]
    cols = [c for c in df.columns if c != T.DAYS_SINCE_RAIN]
    col = b.selectbox("Variable", cols, key="ch_col")
    view = st.radio("Chart", ["Daily", "Year by year", "Wetter or drier than average", "Month by month",
                              "Average year"], horizontal=True, key="ch_view", label_visibility="collapsed")
    yt = T.yearly_table(df)
    mt = T.monthly_table(df)
    if view == "Daily":
        y0, y1 = res.start_year, res.end_year
        if y1 > y0:
            default = (max(y0, y1 - 4), y1)
            y0, y1 = st.slider("Years to show", res.start_year, res.end_year, default, key="ch_years")
        part = df.loc[f"{y0}-01-01":f"{y1}-12-31"]
        st.altair_chart(C.daily(part, col), width="stretch")
        st.caption("Showing a few years keeps the chart quick; widen the years above if you need. "
                   "Drag to move along the time axis and scroll to zoom. Hover to read values.")
    elif view == "Year by year":
        out = C.yearly(yt, col)
        if out:
            chart, mean, slope, p = out
            st.altair_chart(chart, width="stretch")
            if not np.isnan(slope):
                verdict = "a clear trend" if p < 0.05 else "no clear trend"
                st.caption(f"Dashed grey line: long-term average ({mean:.1f}). Dotted black line: trend, "
                           f"{slope * 10:+.2f} per decade. Mann-Kendall p-value {p:.3f}, which means "
                           f"{verdict} (below 0.05 is usually read as clear).")
            else:
                st.caption(f"Dashed grey line: long-term average ({mean:.1f}). A trend needs at least 8 years.")
        else:
            st.info("No complete years to show.")
    elif view == "Wetter or drier than average":
        chart = C.anomaly(yt, col)
        if chart is not None:
            st.altair_chart(chart, width="stretch")
            st.caption("Each bar is how far that year was from the long-term average of the years shown.")
    elif view == "Month by month":
        st.altair_chart(C.heatmap(mt, col), width="stretch")
        st.caption("Each square is one month. Darker means more. Blank squares have missing days.")
    else:
        st.altair_chart(C.average_year(mt, col), width="stretch")


def compare_tab(tables, site_names):
    yearly_all = tables["Yearly summary"]
    cols = [c for c in yearly_all.columns if c not in ("Site", "Year")]
    col = st.selectbox("Variable", cols, key="cmp_col")
    chosen = st.multiselect("Sites (up to 8)", site_names, default=site_names[:8], max_selections=8)
    if chosen:
        st.altair_chart(C.compare_sites(yearly_all, col, chosen), width="stretch")
    st.write("**Long-term averages**")
    show_table(yearly_all.groupby("Site")[cols].mean().round(1).reset_index())


def indices_tab(tables, site_names):
    ri_all = tables["Rain indices"]
    if ri_all.empty:
        st.info("Rain indices need Rainfall to be one of the variables.")
        return
    a, b = st.columns(2)
    site = a.selectbox("Site", site_names, key="ri_site")
    col = b.selectbox("Index", list(T.INDEX_INFO), key="ri_col")
    ri = ri_all[ri_all.Site == site]
    st.altair_chart(C.index_bars(ri, col), width="stretch")
    st.caption(T.INDEX_INFO[col] + " Years with any missing day are left out.")
    show_table(ri.drop(columns="Site"))


def trends_tab(res, tables, site_names):
    tr = tables["Trends"]
    if tr.empty:
        st.info("Trends need at least 8 complete years.")
        return
    st.write("Every yearly value, tested for a steady rise or fall over the years. "
             "**Change per decade** is Sen's slope (the median of the slopes between every pair "
             "of years, so one odd year cannot pull it much). **p-value** comes from the "
             "Mann-Kendall test: below 0.05 is usually read as a clear trend.")
    site = st.selectbox("Site", ["All sites"] + site_names, key="tr_site")
    show_table(tr if site == "All sites" else tr[tr.Site == site])
    if res.start_year < 1951 and "rain" in res.variables:
        st.caption("Early decades use fewer rain gauges, which can affect long-term trends.")


def cell_map_tab(res, site_names):
    if not res.cell_means:
        st.info("No grid cells to show.")
        return
    import branca.colormap as bcm
    a, b = st.columns(2)
    site = a.selectbox("Site", site_names, key="map_site")
    vars_here = [v for (s, v) in res.cell_means if s == site]
    var = b.selectbox("Variable", vars_here, format_func=NAME_OF.get, key="map_var")
    cm = res.cell_means[(site, var)]
    value_col = cm.columns[-1]
    valid = cm[cm["Has data"]].dropna(subset=[value_col])
    s_obj = next(s for s in res.sites if s.name == site)
    ramp = C.ORANGE_RAMP if var in ("tmax", "tmin") else C.BLUE_RAMP
    vmin, vmax = (valid[value_col].min(), valid[value_col].max()) if len(valid) else (0, 1)
    if vmin == vmax:
        vmax = vmin + 1
    cmap = bcm.LinearColormap(ramp, vmin=vmin, vmax=vmax, caption=value_col)
    h = T.VARIABLES[var]["grid"].step / 2
    feats = []
    for la, lo, has, val in zip(cm["Latitude"], cm["Longitude"], cm["Has data"], cm[value_col]):
        ok = bool(has) and not pd.isna(val)
        feats.append({"type": "Feature",
                      "properties": {"fill": cmap(val) if ok else "#d9d9d6", "op": 0.8 if ok else 0.4,
                                     "tip": f"{la:.2f} N, {lo:.2f} E | {val:.1f}" if ok
                                     else "No data (sea or off the grid)"},
                      "geometry": {"type": "Polygon", "coordinates": [[
                          [lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h], [lo - h, la - h]]]}})
    m = folium.Map(control_scale=True)
    folium.GeoJson({"type": "FeatureCollection", "features": feats},
                   style_function=lambda f: {"color": "#ffffff", "weight": 1, "fillColor": f["properties"]["fill"],
                                             "fillOpacity": f["properties"]["op"]},
                   tooltip=folium.GeoJsonTooltip(fields=["tip"], labels=False)).add_to(m)
    if s_obj.is_point:
        folium.Marker([s_obj.geometry.y, s_obj.geometry.x], tooltip=site).add_to(m)
    else:
        folium.GeoJson(s_obj.geometry.__geo_interface__, tooltip=site,
                       style_function=lambda _: {"color": "#0b0b0b", "weight": 2, "fillOpacity": 0}).add_to(m)
    cmap.add_to(m)
    minx, miny, maxx, maxy = s_obj.geometry.bounds
    m.fit_bounds([[miny - 4 * h, minx - 4 * h], [maxy + 4 * h, maxx + 4 * h]])
    st_folium(m, key="result_map", height=460, use_container_width=True, returned_objects=[])
    st.caption(f"Each square is an IMD grid cell coloured by its long-term value "
               f"({value_col.lower()}). Grey squares have no data.")


@st.fragment
def results_section():
    res, tables = ss.result["res"], ss.result["tables"]
    site_names = [s.name for s in res.sites]
    st.divider()
    st.subheader("Results")
    st.caption(f"{len(res.sites)} site(s), {res.start_year} to {res.end_year}. "
               "These are for the settings used when you last pressed the button.")
    downloads_box(res, tables)
    if res.problems:
        with st.expander(f"⚠️ {len(res.problems)} problem(s) getting data", expanded=True):
            for p in res.problems:
                st.write("• " + p)
    if res.notes:
        with st.expander(f"ℹ️ {len(res.notes)} caution(s) about using this data"):
            for n in res.notes:
                st.write("• " + n)

    # Only the chosen section is drawn (tabs would draw every chart at once).
    sections = ["Overview", "Charts", "Rain indices", "Trends", "Map of grid cells"]
    if len(res.sites) > 1:
        sections.insert(2, "Compare sites")
    section = st.segmented_control("Show", sections, default="Overview", key="section",
                                   label_visibility="collapsed") or "Overview"
    if section == "Overview":
        overview_tab(res, tables, site_names)
    elif section == "Charts":
        charts_tab(res, site_names)
    elif section == "Compare sites":
        compare_tab(tables, site_names)
    elif section == "Rain indices":
        indices_tab(tables, site_names)
    elif section == "Trends":
        trends_tab(res, tables, site_names)
    else:
        cell_map_tab(res, site_names)


if ss.result:
    results_section()

st.divider()
st.caption("Data: India Meteorological Department (IMD), Pune. Rainfall 0.25° (Pai et al., 2014); "
           "temperature 1° (Srivastava et al., 2009). Please cite IMD when you use it.")
