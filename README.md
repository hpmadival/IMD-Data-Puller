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
| `requirements.txt` | The Python packages needed |
| `Run in Colab.ipynb` | Starts the app in Google Colab and gives you a link |

## Three ways to run it

### 1. In Google Colab (easiest, nothing to install)
1. Put the whole `imd-data-app` folder in **My Drive** in Google Drive.
2. Open `Run in Colab.ipynb` from Drive with Google Colab.
3. Run the cells from top to bottom and click the link it prints.

The IMD files are kept in `My Drive/IMD data`, so they survive after Colab shuts down.
The link changes each time you start the app.

### 2. On your own laptop
```
pip install -r requirements.txt
streamlit run app.py
```
Your browser opens the app. Downloaded files go into an `imd-data` folder next to the app,
unless you change the data folder in the sidebar.

### 3. Online, so others can use it (Streamlit Community Cloud, free)
1. Put these files in a GitHub repository.
2. Sign in at share.streamlit.io with GitHub, choose the repository, and pick `app.py`.
3. You get a permanent link to share with your lab.

Note: free hosting does not keep downloaded files for long, so pulls there are slower,
and memory is limited (keep requests to a few catchments and a few decades at a time).

## How to use it

1. **Sidebar:** pick the variables, the years, the extra outputs, and the options
   (rainfall date shift, "days since rain").
2. **Where?** choose one of:
   - **Points:** type names and coordinates in the table, click the map to add points, or
     load a CSV file with latitude and longitude columns.
   - **A box:** type the four edges, or draw a rectangle on the map.
   - **Upload boundary file:** a zipped shapefile (or the .shp, .shx, .dbf and .prj parts together),
     GeoJSON, GeoPackage or KML. Pick the column that holds the catchment names.
3. Check the blue grid cells on the map.
4. Press **Get the data**. Look through the tabs (Overview, Charts, Compare sites,
   Rain indices, Trends, Map of grid cells), then download the zip or the Excel workbook.

## How the numbers are worked out

- **Point:** the value of the nearest grid cell that has data. Cells over the sea have no
  data, so a coastal point uses the nearest land cell. The distance is listed in `Grid cells used.csv`.
- **Area:** each cell's weight is the share of the catchment area it covers. On a day when
  a cell is missing, the other cells share out its weight.
- **Monthly and yearly values** are left empty if any day is missing, so totals are never quietly too small.
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

The engine works on its own in any notebook:

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
