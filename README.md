# IMD Data Puller

A small web app that gets India Meteorological Department (IMD) daily gridded data
for a **point**, a **box**, or your own **catchment boundaries**, and gives it back
as ready-to-use files.

## What it gives you

- **Rainfall** (0.25° grid, from 1901), **maximum and minimum temperature** (1° grid, from 1951)
- **Potential evaporation** (Hargreaves method, worked out from the two temperatures)
- **Area-weighted catchment averages:** a grid cell half inside the boundary counts half
- **Many catchments at once:** upload one shapefile and every shape gets its own series
- **Monthly and yearly summaries**, including June to September monsoon rainfall
- **Coverage checks:** how many grid cells were used and how much of the area has data
- **A map preview** of the grid cells that will be used, before you download anything
- **Rain indices** each year: rainy days, heavy and very heavy rain days (IMD definitions),
  wettest day, wettest 5 days, longest dry and wet spells
- **Trends** for every yearly value: change per decade (Sen's slope) and whether it is a
  clear trend (Mann-Kendall test)
- **Charts:** daily (zoomable), year by year with average and trend lines, wetter or drier
  than average, a month-by-month heatmap, the average year, and a comparison of sites
- **A map of grid cells** coloured by their long-term value, to see how rainfall changes across a catchment
- **Rainfall date shift:** move rainfall one day earlier or later to line up with streamflow records
- **"Days since rain" column** for picking dry-weather (recession) periods
- **Cautions** written in plain words, e.g. when a catchment gets its temperature from a
  single 110 km cell, or part of it lies over the sea
- **Points from a CSV file**, for many gauges at once
- Downloads: a zip of CSV files and an **Excel workbook**; optional **every grid cell's daily
  values** (CSV) and the **cut-out grid** (NetCDF)
- A **settings file** recording exactly what was pulled, for repeatability

Downloaded IMD files are saved in a data folder and reused, so each year is downloaded only once.

## The files

| File | What it does |
|---|---|
| `app.py` | The screens: choosing data, the map, charts and download buttons |
| `imd_tools.py` | The engine: downloading, reading, averaging, summaries, indices, trends, downloads |
| `charts.py` | The charts |
| `india_boundary.geojson` | India's boundary for the map (simplified from the India country boundary shapefile) |
| `.streamlit/config.toml` | The app's colours and font |
| `requirements.txt` | The Python packages needed |

## Two ways to run it

### 1. Online (easiest): open the web link < https://imd-data-puller-app.streamlit.app/ >
The app runs on Streamlit Community Cloud. Open its link in any browser; nothing to install.

- If nobody has used it for a few days, it goes to sleep. The first visit then takes about a minute.
- Downloaded IMD files are not kept for long on the free hosting, so pulls there are slower.
- Memory is limited, so keep requests to a few catchments and a few decades at a time.
- Any change saved to this GitHub repository updates the app by itself within a few minutes.

To set up your own copy: sign in at share.streamlit.io with GitHub, choose this repository,
branch `main` and file `app.py`, pick Python 3.12 under Advanced settings, and click Deploy.

### 2. On your own laptop (fastest, best for big pulls)
You need Python 3.10 or newer (from python.org). Download this repository (on this page,
**Code → Download ZIP**, then unzip it), open a terminal in that folder, and run:
```
pip install -r requirements.txt
streamlit run app.py
```
Your browser opens the app. Downloaded IMD files go into an `imd-data` folder next to the app
and are reused next time, so each year is downloaded only once.

## How to use it

The screen works like a Google Earth Engine app: the control panel on the left, the map on the right,
and the results below.

1. **Choose places on the map.** Use the tools on the map's left edge: 📍 a point, ▭ a box, or
   ⬠ draw your catchment's outline. 🔍 searches for a place name. The coordinates under the mouse
   show in the bottom-left corner. You can also:
   - **upload a boundary file:** a zipped shapefile (or the .shp, .shx, .dbf and .prj parts together),
     GeoJSON, GeoPackage or KML, then pick the column that holds the names;
   - **type coordinates** or **load a CSV** of points (latitude and longitude columns).
   Every place appears in the **Places** list under the map, where you can rename or remove it.
   The blue squares show the IMD grid cells that will be used (darker = more of the cell is inside).
2. **Left panel:** pick the variables, the period (1991 to 2020, the last 10 years, the full record,
   or **Choose dates**: exact days picked on a calendar, shown as dd/mm/yyyy), and any extra outputs. Rarely needed settings (rainfall date shift, "days since
   rain", data folder) are under **Advanced options**. Nothing reloads while you change these.
3. Press **Get the data**. The app says how many files it must download from IMD and roughly how long
   it will take; files are saved and reused next time.
4. **Results:** headline numbers for each site, then the sections Overview, Charts, Compare sites,
   Rain indices, Trends and Map of grid cells. Download the zip of CSV files or make the Excel workbook.

New here? Press **✨ Try an example** at the top right.

The map shows India's boundary from the India country boundary shapefile (simplified for the web)
and the IMD grid as faint lines. Switch between the rainfall grid (0.25°) and the temperature grid
(1°), and between the light, street and satellite base maps. Background map tiles come from third
parties and may draw borders differently; the dark outline is the one to go by.

## How the numbers are worked out

- **Point:** the value of the nearest grid cell that has data. Cells over the sea have no
  data, so a coastal point uses the nearest land cell. The distance is listed in `Grid cells used.csv`.
- **Area:** each cell's weight is the share of the catchment area it covers. On a day when
  a cell is missing, the other cells share out its weight.
- **Monthly and yearly values** are left empty if any day is missing, or if the month or year is only
  partly inside your chosen dates, so totals are never quietly too small.
- **Potential evaporation (Hargreaves, FAO-56):**
  0.0023 × Ra × (mean temperature + 17.8) × square root of (max − min temperature),
  where Ra is the sun's radiation at the top of the atmosphere for that latitude and day.
  It uses temperature only, so treat it as an estimate.
- **Rain indices:** rainy day 2.5 mm or more, heavy 64.5 mm or more, very heavy 115.6 mm or
  more (IMD). Dry spell: days in a row under 1 mm; wet spell: 1 mm or more (ETCCDI).
- **Trends:** Sen's slope is the median of the slopes between every pair of years, so one odd
  year cannot pull it much. A Mann-Kendall p-value below 0.05 is usually read as a clear
  trend. At least 8 years are needed.
- **Dryness index** = mean annual potential evaporation ÷ mean annual rainfall
  (above 1 means drier, below 1 means wetter).

## Using the engine without the app

The engine works on its own in any Python script or notebook on your laptop:

```python
import imd_tools as T
from shapely.geometry import Point

gdf, notes = T.read_boundary_files([("basins.zip", open("basins.zip", "rb").read())])
sites = T.sites_from_frame(gdf, "Basin name") + [T.Site("Mangalore", Point(74.86, 12.91))]
res = T.pull_data(sites, ["rain"], 1981, 2020, "imd-data", add_pet=True, days_since_rain_mm=1.0)
res.daily["Mangalore"].head()          # daily table
tables = T.all_tables(res)             # summaries, rain indices, trends, cells
open("results.zip", "wb").write(T.build_zip(res, tables))
open("results.xlsx", "wb").write(T.build_excel(res, tables))
```

## Data credit

IMD Pune gridded data. Rainfall: Pai et al. (2014), Mausam 65(1). Temperature: Srivastava
et al. (2009), Atmospheric Science Letters 10. Downloading uses the same IMD addresses as
the imdlib package (Nandi, Patel and Swain), so updating imdlib also updates those addresses.
