"""
IMD Data Puller: a small web app to get IMD gridded rainfall and temperature
for points, boxes, or catchment boundaries, and download it.

Layout (like a Google Earth Engine app):
  left   - the control panel: what, when, extras, advanced options, Get the data
  right  - the map: draw points, boxes or outlines, search places, upload boundaries
  below  - the results: summary cards, charts, tables and downloads

Run it with:   streamlit run app.py
"""

import datetime
import json
import os

import folium
import numpy as np
import pandas as pd
import shapely
import streamlit as st
from folium.plugins import Draw, Fullscreen, Geocoder, MousePosition
from shapely.geometry import Point, box, mapping, shape
from streamlit_folium import st_folium

import charts as C
import imd_tools as T

st.set_page_config(page_title="IMD Data Puller", page_icon="🌧️", layout="wide",
                   initial_sidebar_state="collapsed")

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date.today()
THIS_YEAR = TODAY.year
LAST_YEAR = THIS_YEAR - 1 if TODAY.month >= 6 else THIS_YEAR - 2   # latest year IMD has usually published
NAME_OF = {v: T.VARIABLES[v]["name"] for v in T.VARIABLES}
INDIA_CENTRE, INDIA_ZOOM = [22.5, 80.5], 5
ss = st.session_state
ss.setdefault("places", [])          # list of {"name", "geojson"}
ss.setdefault("map_version", 0)      # bump to redraw the map from scratch
ss.setdefault("result", None)
ss.setdefault("f_vars", ["rain"])
ss.setdefault("f_pet", False)
ss.setdefault("f_years_choice", "Last 10 years")

# --- a cleaner look: hide Streamlit's own menu, header and footer, tighten spacing ---
st.markdown("""
<style>
footer {visibility: hidden; height: 0;}
header[data-testid="stHeader"] {background: transparent;}
.block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px;}
div[data-testid="stMetric"] {background: rgba(42,120,214,0.07); border: 1px solid rgba(128,140,155,0.28);
                             border-radius: 10px;
                             padding: 10px 14px;}
div[data-testid="stMetricLabel"] p {font-size: 0.85rem; opacity: 0.75;}
.panel-title {font-size: 0.8rem; letter-spacing: .04em; opacity: 0.65; margin: 0.6rem 0 0.1rem 0;}
.hint {font-size: 0.92rem; opacity: 0.8; margin-bottom: 0.3rem;}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Cached helpers (so a click doesn't redo slow work)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def india_layers():
    """India's boundary (simplified from the official shapefile) and the two IMD
    grids drawn as thin lines, clipped to India. Worked out once per server."""
    with open(os.path.join(HERE, "india_boundary.geojson"), encoding="utf-8") as f:
        india = json.load(f)
    india_geom = shape(india["geometry"])
    area = shapely.simplify(india_geom.buffer(0.05), 0.05)
    grids = {}
    for key, grid in (("rain", T.RAIN_GRID), ("tmax", T.TEMP_GRID)):
        x0, y0, x1, y1 = grid.bounds
        xs = np.arange(x0, x1 + 1e-9, grid.step)
        ys = np.arange(y0, y1 + 1e-9, grid.step)
        lines = np.array([shapely.LineString([(x, y0), (x, y1)]) for x in xs] +
                         [shapely.LineString([(x0, y), (x1, y)]) for y in ys])
        # cut each line to India on its own (cutting them together chops every line at every
        # crossing and makes the map about 25 times heavier)
        parts = shapely.get_parts(shapely.intersection(lines, area))
        parts = parts[(shapely.get_type_id(parts) == 1) & ~shapely.is_empty(parts)]
        clipped = shapely.set_precision(shapely.MultiLineString(list(parts)), 0.001)
        grids[key] = {"type": "Feature", "properties": {}, "geometry": mapping(clipped)}
    # a lighter copy of the boundary for the map (looks the same at map scale)
    india = {"type": "Feature", "properties": {},
             "geometry": mapping(shapely.set_precision(shapely.simplify(india_geom, 0.01), 0.001))}
    return india, grids


@st.cache_data(ttl=60, show_spinner=False)
def saved_counts(data_dir, backup_dir):
    return {v: T.saved_count(data_dir, v, backup_dir) for v in T.VARIABLES}


def files_to_download(need, years, data_dir, backup_dir):
    """How many yearly files must come from IMD, and a rough time for it."""
    todo = [(v, y) for v in need for y in years
            if y >= T.VARIABLES[v]["first_year"] and not T.is_available(data_dir, v, y, backup_dir)]
    rain = sum(1 for v, _ in todo if v == "rain")
    seconds = (rain * 20 + (len(todo) - rain) * 4) / 3          # three at a time
    return len(todo), seconds


def nice_time(seconds):
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    return f"about {minutes} minute{'s' if minutes != 1 else ''}"


@st.cache_data(show_spinner=False, max_entries=500)
def preview_cells(wkb, var):
    from shapely import wkb as W
    return T.cells_for_site(T.Site("x", W.loads(wkb)), var)


@st.cache_data(show_spinner=False, max_entries=1000)
def area_of(wkb):
    from shapely import wkb as W
    return T.area_km2(W.loads(wkb))


def dark_mode():
    """True when the visitor is viewing the app in dark mode."""
    try:
        return (st.context.theme.type or "light") == "dark"
    except Exception:
        return False


def current_sites():
    used = set()
    return [T.Site(T.clean_name(p["name"], used), shape(p["geojson"])) for p in ss.places]


def add_place(name, geom):
    used = {p["name"] for p in ss.places}
    ss.places.append({"name": T.clean_name(name, used), "geojson": mapping(geom)})
    ss.map_version += 1


def refresh_map_area():
    """Redraw just the map area; fall back to the whole page if that isn't possible."""
    from streamlit.errors import StreamlitInvalidLayoutContextError
    try:
        st.rerun(scope="fragment")
    except StreamlitInvalidLayoutContextError:
        st.rerun()


def next_name(kind):
    n = sum(1 for p in ss.places if p["name"].startswith(kind)) + 1
    return f"{kind} {n}"


def cells_geojson(sites, var):
    """Grid cells used by the chosen places, as one layer (fast to draw)."""
    h = T.VARIABLES[var]["grid"].step / 2
    feats = []
    for s in sites:
        cells = preview_cells(s.geometry.wkb, var)
        for la, lo, fr in zip(cells["Latitude"], cells["Longitude"], cells["Fraction of cell inside"]):
            frac = 1.0 if s.is_point else float(fr)
            tip = f"Cell {la:.2f} N, {lo:.2f} E" + ("" if s.is_point else f" | {frac * 100:.0f}% inside")
            feats.append({"type": "Feature",
                          "properties": {"tip": tip, "op": round(0.12 + 0.45 * min(frac, 1.0), 3)},
                          "geometry": {"type": "Polygon", "coordinates": [[
                              [lo - h, la - h], [lo + h, la - h], [lo + h, la + h], [lo - h, la + h],
                              [lo - h, la - h]]]}})
    return {"type": "FeatureCollection", "features": feats}


# ---------------------------------------------------------------------------
# "Try an example" (applied before the widgets are drawn)
# ---------------------------------------------------------------------------
if ss.pop("load_example", False):
    ss.places = []
    add_place("Mangaluru", Point(74.856, 12.914))
    add_place("Upper Netravati area", box(75.20, 12.55, 75.75, 13.05))
    ss["f_vars"] = ["rain", "tmax", "tmin"]
    ss["f_pet"] = True
    ss["f_years_choice"] = "Last 10 years"


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
head_l, head_r = st.columns([3, 1])
head_l.markdown("## 🌧️ IMD Data Puller")
head_l.markdown("<div class='hint'>Daily gridded rainfall and temperature from the India Meteorological "
                "Department, for points, boxes or your own catchments, with charts and ready-to-use "
                "downloads.</div>", unsafe_allow_html=True)
if head_r.button("✨ Try an example", width="stretch",
                 help="Loads a sample point and area near Mangaluru, with the last 10 years."):
    ss.load_example = True
    st.rerun()
head_r.caption("🌗 Light, dark or system theme: open the ⋮ menu at the top right.")

panel, mapcol = st.columns([1, 2.6], gap="medium")
data_dir_default = os.environ.get("IMD_DATA_DIR", "imd-data")
backup_dir = os.environ.get("IMD_BACKUP_DIR") or None


# ---------------------------------------------------------------------------
# Left: the control panel (a form, so nothing reloads until you press the button)
# ---------------------------------------------------------------------------
with panel:
    with st.form("controls", border=True):
        st.markdown("<div class='panel-title'>1 · WHAT</div>", unsafe_allow_html=True)
        variables = st.multiselect("Variables", list(T.VARIABLES),
                                   format_func=NAME_OF.get, key="f_vars", label_visibility="collapsed",
                                   placeholder="Choose variables")
        add_pet = st.checkbox("Add potential evaporation (Hargreaves)", key="f_pet",
                              help="An estimate of how much water the air could evaporate each day, from "
                                   "maximum and minimum temperature. Both are pulled automatically.")

        st.markdown("<div class='panel-title'>2 · WHEN</div>", unsafe_allow_html=True)
        years_choice = st.radio("Period", ["1991 to 2020", "Last 10 years", "Full record", "Choose dates"],
                                horizontal=True, key="f_years_choice", label_visibility="collapsed")
        picked = st.date_input("Dates (used with 'Choose dates')",
                               value=(datetime.date(LAST_YEAR, 6, 1), datetime.date(LAST_YEAR, 9, 30)),
                               min_value=datetime.date(1901, 1, 1), max_value=TODAY,
                               format="DD/MM/YYYY", key="f_dates",
                               help="Click the box to open the calendar, then click the first day and the "
                                    "last day. You can also type dates as dd/mm/yyyy.")
        st.caption("Rainfall starts on 01/01/1901, temperature on 01/01/1951. A year's data appears a "
                   "few months after it ends.")

        st.markdown("<div class='panel-title'>3 · EXTRA OUTPUTS</div>", unsafe_allow_html=True)
        keep_cells = st.checkbox("Daily values of every grid cell (CSV)", key="f_cells",
                                 help="Each grid cell's values before averaging, one column per cell.")
        keep_grid = st.checkbox("Cut-out grid (NetCDF)", key="f_grid",
                                help="The grid around your places, for GIS or Python.")

        with st.expander("Advanced options"):
            shift_label = st.selectbox(
                "Rainfall dates", list(T.DATE_SHIFT_TEXT.values()), index=0, key="f_shift",
                help="Only for matching rainfall day by day with streamflow or other records. IMD's rain "
                     "day runs from 8:30 am to 8:30 am; if your other data files the same day under a "
                     "different date, move rainfall by one day. Most users leave this alone.")
            add_dsr = st.checkbox("Add a 'Days since rain' column", key="f_dsr",
                                  help="Days since the last rainy day. Handy for picking dry-weather "
                                       "(recession) periods.")
            dsr_mm = st.number_input("A day counts as rainy from (mm)", min_value=0.1, value=1.0,
                                     step=0.5, key="f_dsr_mm")
            data_dir = st.text_input("Data folder", value=data_dir_default, key="f_data_dir",
                                     help="Where downloaded IMD files are kept and reused. Change it only "
                                          "to reuse IMD files you already have on this computer.")
        submitted = st.form_submit_button("Get the data", type="primary", width="stretch")

    saved = saved_counts(data_dir, backup_dir)
    st.caption("Years saved on this server: " +
               ", ".join(f"{NAME_OF[v].lower()} {n}" for v, n in saved.items()))

rain_shift = {v: k for k, v in T.DATE_SHIFT_TEXT.items()}[shift_label]


# ---------------------------------------------------------------------------
# Right: the map and the list of places (a fragment: map clicks redraw only this part)
# ---------------------------------------------------------------------------
def build_map(sites, grid_var, base):
    india, grids = india_layers()
    m = folium.Map(location=INDIA_CENTRE, zoom_start=INDIA_ZOOM, tiles=None, control_scale=True,
                   min_zoom=4, prefer_canvas=True)
    # folium loads jQuery, Bootstrap and icon fonts by default; this map uses none of them,
    # so leave them out (fewer files for the browser to fetch)
    m.default_js = [js for js in m.default_js if js[0] == "leaflet"]
    m.default_css = [css for css in m.default_css if css[0] == "leaflet_css"]
    if base == "Light map":
        folium.TileLayer("Esri.WorldGrayCanvas", name="Light map").add_to(m)
    elif base == "Dark map":
        folium.TileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
            attr="Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ", name="Dark map", max_zoom=16).add_to(m)
    elif base == "Satellite":
        folium.TileLayer("Esri.WorldImagery", name="Satellite").add_to(m)
    else:
        folium.TileLayer("OpenStreetMap", name="Street map").add_to(m)
    on_dark = base in ("Satellite", "Dark map")
    line = "#ffffff" if base == "Satellite" else ("#aab2bd" if base == "Dark map" else "#8a8f98")
    folium.GeoJson(grids[grid_var], name="IMD grid", interactive=False,
                   style_function=lambda _: {"color": line, "weight": 0.5, "opacity": 0.45}).add_to(m)
    folium.GeoJson(india, name="India boundary", interactive=False,
                   style_function=lambda _: {"color": "#e6e9ee" if on_dark else "#3d4652",
                                             "weight": 1.3, "fill": False}).add_to(m)
    gj = cells_geojson(sites, grid_var)
    if 0 < len(gj["features"]) <= 20000:
        folium.GeoJson(gj, name="Grid cells used",
                       style_function=lambda f: {"color": "#2a78d6", "weight": 0.8, "fillColor": "#2a78d6",
                                                 "fillOpacity": f["properties"]["op"]},
                       tooltip=folium.GeoJsonTooltip(fields=["tip"], labels=False)).add_to(m)
    for s in sites:
        if s.is_point:
            folium.CircleMarker([s.geometry.y, s.geometry.x], radius=6, color="#ffffff", weight=2,
                                fill=True, fill_color="#eb6834", fill_opacity=1, tooltip=s.name).add_to(m)
        else:
            folium.GeoJson(mapping(s.geometry), tooltip=s.name,
                           style_function=lambda _: {"color": "#eb6834", "weight": 2.2,
                                                     "fillOpacity": 0.04}).add_to(m)
    Draw(position="topleft",
         draw_options={"marker": True, "rectangle": True, "polygon": {"allowIntersection": False},
                       "polyline": False, "circle": False, "circlemarker": False},
         edit_options={"edit": False, "remove": False}).add_to(m)
    Geocoder(position="topright", collapsed=True, add_marker=False).add_to(m)
    MousePosition(position="bottomleft", separator=" N,  ", num_digits=4, prefix="",
                  lat_formatter="function(n){return L.Util.formatNum(n,4)}",
                  lng_formatter="function(n){return L.Util.formatNum(n,4) + ' E'}").add_to(m)
    Fullscreen(position="topleft").add_to(m)
    if sites:
        minx, miny, maxx, maxy = np.array([s.geometry.bounds for s in sites]).T
        pad = 0.4
        m.fit_bounds([[miny.min() - pad, minx.min() - pad], [maxy.max() + pad, maxx.max() + pad]])
    return m


@st.fragment
def map_section():
    sites = current_sites()
    t1, t2 = st.columns([1, 1.3])
    grid_label = t1.radio("Grid", ["Rainfall grid (0.25°)", "Temperature grid (1°)"], horizontal=True,
                          key="grid_choice", label_visibility="collapsed")
    bases = ["Light map", "Dark map", "Street map", "Satellite"]
    if "base_choice" not in ss:
        ss.base_choice = "Dark map" if dark_mode() else "Light map"
    base = t2.radio("Base map", bases, horizontal=True, key="base_choice", label_visibility="collapsed")
    grid_var = "rain" if grid_label.startswith("Rain") else "tmax"
    if not sites:
        st.markdown("<div class='hint'>Use the tools on the map's left edge: 📍 a point, ▭ a box, or "
                    "⬠ draw your catchment's outline. Search a place with 🔍. Or upload a boundary "
                    "file or a list of points below.</div>", unsafe_allow_html=True)
    out = st_folium(build_map(sites, grid_var, base), key=f"map_{ss.map_version}", height=560,
                    use_container_width=True, returned_objects=["last_active_drawing"])
    drawing = (out or {}).get("last_active_drawing")
    if drawing and drawing.get("geometry"):
        sig = json.dumps(drawing["geometry"], sort_keys=True)
        if sig != ss.get("seen_drawing"):
            ss.seen_drawing = sig
            geom = shape(drawing["geometry"])
            if geom.geom_type == "Point":
                add_place(next_name("Point"), geom)
            elif abs(geom.area - geom.envelope.area) < 1e-9:
                add_place(next_name("Box"), geom)
            else:
                add_place(next_name("Area"), shapely.make_valid(geom))
            refresh_map_area()

    # --- the places chosen so far ---
    if sites:
        st.markdown(f"**Places ({len(sites)})** · rename by typing in the Name column, tick Remove to drop one")
        table = pd.DataFrame({
            "Name": [s.name for s in sites],
            "Type": ["Point" if s.is_point else "Area" for s in sites],
            "Latitude": [round(s.centre[0], 4) for s in sites],
            "Longitude": [round(s.centre[1], 4) for s in sites],
            "Area (km²)": ["" if s.is_point else f"{area_of(s.geometry.wkb):,.1f}" for s in sites],
            "Remove": [False] * len(sites)})
        edited = st.data_editor(table, key=f"places_{ss.map_version}", hide_index=True, width="stretch",
                                disabled=["Type", "Latitude", "Longitude", "Area (km²)"],
                                height=min(320, 38 + 35 * len(sites)))
        a, b = st.columns([1, 1])
        if a.button("Apply changes to the list", width="stretch"):
            keep = []
            for p, (_, row) in zip(ss.places, edited.iterrows()):
                if not row["Remove"]:
                    keep.append({"name": str(row["Name"]).strip() or p["name"], "geojson": p["geojson"]})
            ss.places = keep
            ss.map_version += 1
            refresh_map_area()
        if b.button("Clear all places", width="stretch"):
            ss.places = []
            ss.map_version += 1
            refresh_map_area()

    # --- other ways to add places ---
    with st.expander("Upload a boundary file (shapefile, GeoJSON, GeoPackage or KML)"):
        files = st.file_uploader(
            "Zipped shapefile, or the .shp .shx .dbf .prj parts together, or GeoJSON / GeoPackage / KML",
            accept_multiple_files=True, key="boundary_upload",
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
                new_sites = T.sites_from_frame(gdf, name_col if cols else None)
                if st.button(f"Add these {len(new_sites)} shape(s) to the map", type="primary"):
                    for s in new_sites:
                        add_place(s.name, s.geometry)
                    refresh_map_area()

    with st.expander("Add points by typing coordinates, or from a CSV file"):
        c1, c2, c3, c4 = st.columns([1.3, 1, 1, 0.7])
        pname = c1.text_input("Name", placeholder="e.g. Udupi")
        plat = c2.number_input("Latitude", -90.0, 90.0, value=None, format="%.4f", placeholder="13.3409")
        plon = c3.number_input("Longitude", -180.0, 180.0, value=None, format="%.4f", placeholder="74.7421")
        c4.write("")
        if c4.button("Add", width="stretch") and plat is not None and plon is not None:
            add_place(pname.strip() or next_name("Point"), Point(plon, plat))
            refresh_map_area()
        pts_file = st.file_uploader("CSV with latitude and longitude columns (and ideally names)",
                                    type=["csv", "txt"], key="points_upload")
        if pts_file is not None and ss.get("pts_file_key") != (pts_file.name, pts_file.size):
            try:
                table = T.points_from_table(pd.read_csv(pts_file))
                for r in table.itertuples(index=False):
                    add_place(r.Name, Point(r.Longitude, r.Latitude))
                ss.pts_file_key = (pts_file.name, pts_file.size)
                refresh_map_area()
            except Exception as err:
                st.error(f"Could not read the points: {err}")


with mapcol:
    map_section()


# ---------------------------------------------------------------------------
# Get the data (runs only when the form's button is pressed)
# ---------------------------------------------------------------------------
if years_choice == "1991 to 2020":
    start_year, end_year = 1991, 2020
elif years_choice == "Last 10 years":
    start_year, end_year = LAST_YEAR - 9, LAST_YEAR
elif years_choice == "Full record":
    need_first = [T.VARIABLES[v]["first_year"] for v in (set(variables) | ({"tmax", "tmin"} if add_pet else set()))]
    start_year, end_year = (max(need_first) if need_first else 1901), LAST_YEAR
else:
    start_year = end_year = None
date_problem = None
if years_choice == "Choose dates":
    if isinstance(picked, (tuple, list)) and len(picked) == 2:
        start_date, end_date = picked
        start_year, end_year = start_date.year, end_date.year
    else:
        date_problem = "Pick both a first and a last day on the calendar."
        start_date = end_date = None
else:
    start_date, end_date = datetime.date(start_year, 1, 1), datetime.date(end_year, 12, 31)

if submitted:
    sites = current_sites()
    problems = []
    if not sites:
        problems.append("Add at least one place on the map first.")
    if not variables and not add_pet:
        problems.append("Choose at least one variable.")
    if date_problem:
        problems.append(date_problem)
    elif start_date > end_date:
        problems.append("The first day is after the last day.")
    if add_dsr and "rain" not in variables:
        problems.append("'Days since rain' needs Rainfall to be one of the variables.")
    outside = T.check_inside_india(sites) if sites else []
    if outside:
        problems.append("These are outside IMD's India grid: " + ", ".join(outside))
    if problems:
        for p in problems:
            st.warning(p)
    else:
        need = sorted(set(variables) | ({"tmax", "tmin"} if add_pet else set()))
        n_files, secs = files_to_download(need, range(start_year, end_year + 1), data_dir, backup_dir)
        first_msg = (f"Downloading {n_files} yearly file(s) from IMD, {nice_time(secs)}. "
                     "They are saved for next time." if n_files else "All files are already saved. Reading them.")
        bar = st.progress(0.0, text=first_msg)
        try:
            res = T.pull_data(sites, variables, start_year, end_year, data_dir,
                              add_pet=add_pet, keep_cell_values=keep_cells, keep_grid=keep_grid,
                              rain_shift=rain_shift, days_since_rain_mm=dsr_mm if add_dsr else None,
                              backup_dir=backup_dir, start_date=start_date, end_date=end_date,
                              progress=lambda f, msg: bar.progress(min(max(f, 0.0), 1.0),
                                                                   text=f"{msg}  ({first_msg})" if n_files else msg))
            bar.progress(1.0, text="Working out summaries and packing the files")
            tables = T.all_tables(res)
            ss.result = {"res": res, "tables": tables, "zip": T.build_zip(res, tables), "excel": None}
            saved_counts.clear()
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
    period = f"{res.start_date:%d-%m-%Y} to {res.end_date:%d-%m-%Y}"
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


def summary_cards(res, tables, site):
    """Four headline numbers for one site."""
    df = res.daily[site]
    yt = T.yearly_table(df)
    tr = tables["Trends"]
    tr = tr[tr.Site == site] if len(tr) else tr
    rain = T.VARIABLES["rain"]["column"]
    cards = []
    if rain in df and yt[rain].notna().any():
        cards.append(("Mean annual rainfall", f"{yt[rain].mean():,.0f} mm", "Average of the complete years."))
        mons = "Monsoon rainfall, June to September (mm)"
        share = 100 * yt[mons].sum() / yt[rain].sum() if yt[rain].sum() else np.nan
        cards.append(("Monsoon share", f"{share:.0f}%", "Share of the year's rain from June to September."))
        ri = tables["Rain indices"]
        ri = ri[ri.Site == site] if len(ri) else ri
        if len(ri) and ri["Wettest day (mm)"].notna().any():
            i = ri["Wettest day (mm)"].idxmax()
            day = df.loc[df.index.year == int(ri.loc[i, "Year"]), rain].idxmax()
            cards.append(("Wettest day", f"{ri.loc[i, 'Wettest day (mm)']:.0f} mm",
                          f"On {day:%d %b %Y}."))
        main = rain
    elif rain in df and df[rain].notna().any():
        r = df[rain]
        cards.append(("Total rainfall", f"{r.sum():,.0f} mm", "Over the chosen dates."))
        cards.append(("Rainy days", f"{int((r >= T.RAINY_DAY_MM).sum())}", "Days with 2.5 mm or more."))
        cards.append(("Wettest day", f"{r.max():.0f} mm", f"On {r.idxmax():%d %b %Y}."))
        main = rain
    else:
        for v in ("tmax", "tmin"):
            col = T.VARIABLES[v]["column"]
            if col in df:
                cards.append((f"Mean {T.VARIABLES[v]['name'].lower()}", f"{df[col].mean():.1f} °C",
                              "Average over all days."))
        main = next((T.VARIABLES[v]["column"] for v in ("tmax", "tmin") if T.VARIABLES[v]["column"] in df), None)
    row = tr[tr.Measure == main] if (len(tr) and main) else pd.DataFrame()
    if len(row):
        r0 = row.iloc[0]
        unit = "mm" if main == rain else "°C"
        verdict = "clear trend" if r0["Clear trend? (p below 0.05)"].startswith("Yes") else "no clear trend"
        cards.append(("Trend per decade", f"{r0['Change per decade']:+.1f} {unit}",
                      f"{verdict.capitalize()} (Mann-Kendall p = {r0['p-value']:.2f})."))
    else:
        cards.append(("Trend per decade", "–", "Needs at least 8 complete years."))
    cols = st.columns(len(cards))
    for col, (label, value, note) in zip(cols, cards):
        col.metric(label, value, help=note)
        col.caption(note)


def overview_tab(res, tables, site_names):
    a, b = st.columns([2, 1])
    site = a.selectbox("Site", site_names, key="ov_site")
    b.write("")
    b.download_button(f"⬇️ Daily data for {site} (CSV)",
                      res.daily[site].to_csv(date_format="%Y-%m-%d").encode("utf-8-sig"),
                      file_name=f"{T.safe_file_name(site)} daily.csv", mime="text/csv", width="stretch")
    summary_cards(res, tables, site)
    st.write("**Daily data**")
    show_table(res.daily[site].reset_index(), height=260)
    st.write("**Summary of all sites**")
    show_table(tables["Summary by site"])


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
                st.caption(f"Dashed grey line: long-term average ({mean:.1f}). Dotted orange line: trend, "
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


def design_tab(res, tables, site_names):
    import analysis as A
    st.write("The rainfall you can expect **once in 2, 5, 10, 25, 50 or 100 years**, for 1, 2, 3 and 5 days "
             "in a row. Engineers use these for culverts, check dams and drains. 'Once in 25 years' means a "
             "4% chance in any one year, not exactly every 25 years.")
    a, b = st.columns(2)
    site = a.selectbox("Site", site_names, key="dr_site")
    dur = b.selectbox("Rain over", [1, 2, 3, 5], format_func=lambda d: f"{d} day" + ("s" if d > 1 else ""),
                      key="dr_dur")
    df = res.daily[site]
    table, notes = A.design_rainfall(df)
    if table.empty:
        st.info(" ".join(notes))
        return
    am = A.annual_maxima(df)
    st.altair_chart(C.return_levels(am, dur), width="stretch")
    st.caption("Dots: each year's largest rainfall, placed at how often it is matched or beaten. Lines: two "
               "standard curves fitted to the dots by L-moments. Where they differ, the 50 and 100-year values "
               "are less certain.")
    show_table(table)
    for n in notes:
        st.caption("• " + n)
    with st.expander("Largest rainfall of each year"):
        show_table(am)


def drought_tab(res, tables, site_names):
    import analysis as A
    st.write("The **Standardised Precipitation Index (SPI)** shows how unusually wet or dry each period was "
             "compared with the years you pulled: 0 is normal, −1 or less is dry, +1 or more is wet. "
             "The 3-month index follows seasonal droughts, the 12-month index longer dry spells.")
    a, b = st.columns(2)
    site = a.selectbox("Site", site_names, key="spi_site")
    scale = b.selectbox("Time scale", A.SPI_SCALES, index=1, format_func=lambda k: f"{k}-month",
                        key="spi_scale")
    sp, notes = A.spi(res.daily[site])
    if sp.empty:
        st.info(" ".join(notes))
        return
    st.altair_chart(C.spi_bars(sp, scale), width="stretch")
    ev = A.drought_periods(sp, scale)
    st.write(f"**Drought periods** ({scale}-month SPI of −1 or less), most severe first")
    if len(ev):
        show_table(ev)
    else:
        st.caption("No drought periods in this time span.")
    for n in notes:
        st.caption("• " + n)
    with st.expander("SPI month by month"):
        show_table(sp)


def report_tab(res, tables, site_names):
    st.write("A short **Word report** for one site, written in plain English, with a map, charts and tables: "
             "the place, rainfall at a glance, temperature and evaporation, heavy rainfall for design, "
             "droughts, cautions, and the data credits. Ready to edit and paste into a thesis or report.")
    site = st.selectbox("Site", site_names, key="rep_site")
    reports = ss.result.setdefault("reports", {})
    if site not in reports:
        if st.button(f"Make the report for {site}", type="primary"):
            import report as R
            with st.spinner("Writing the report"):
                reports[site] = R.build_report(res, tables, site)
    if site in reports:
        st.download_button(f"⬇️ Report for {site} (Word)", reports[site],
                           file_name=f"{T.safe_file_name(site)} rainfall report.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                           type="primary")
        st.caption("Opens in Word, Google Docs or LibreOffice. Check the wording and add your own discussion.")


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
    st.caption(f"{len(res.sites)} site(s), {res.start_date:%d/%m/%Y} to {res.end_date:%d/%m/%Y}. "
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
    sections = ["Overview", "Charts", "Rain indices", "Trends", "Design rainfall", "Drought index",
                "Map of grid cells", "Report"]
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
    elif section == "Design rainfall":
        design_tab(res, tables, site_names)
    elif section == "Drought index":
        drought_tab(res, tables, site_names)
    elif section == "Report":
        report_tab(res, tables, site_names)
    elif section == "Trends":
        trends_tab(res, tables, site_names)
    else:
        cell_map_tab(res, site_names)


if ss.result:
    results_section()

st.divider()
st.caption("Data: India Meteorological Department (IMD), Pune. Rainfall 0.25° (Pai et al., 2014); "
           "temperature 1° (Srivastava et al., 2009). Please cite IMD when you use it. "
           "The dark outline is India's boundary from the India country boundary shapefile, simplified "
           "for the web; background map tiles come from third parties and may draw borders differently.")
