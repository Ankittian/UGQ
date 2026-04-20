# HVAC Comfort — Data Ingestion & Feature Engineering Pipeline

This repository contains the data ingestion and transformation pipeline (`data_ingestion.py`) for the HVAC Comfort tracking project. It merges real-time IoT sensor data (environmental and energy metrics) stored in a Neon Postgres Database with user-reported thermal comfort surveys from a Google Form. 

The output is a robust, PMV-ready feature matrix (`merged_comfort_dataset.csv`) that can be used for supervised machine learning and HVAC optimization.

---

## 🛠️ Installation & Setup

1. **Prerequisites**: Ensure you have Python 3.9+ installed.
2. **Virtual Environment**: It is highly recommended to run this inside a virtual environment.
   ```bash
   python -m venv env
   
   # Windows
   .\env\Scripts\activate
   # Mac/Linux
   source env/bin/activate
   ```
3. **Install Dependencies**:
   Install the required data science and database connection libraries:
   ```bash
   pip install pandas numpy sqlalchemy psycopg2-binary
   ```

4. **Environment Variables (Optional)**:
   By default, the script falls back to hardcoded NeonDB connection strings and local CSV names. For security, you can set them in your active terminal:
   ```bash
   set NEON_DB_URL="Connection string here"
   set GOOGLE_FORM_CSV="form_responses.csv"
   ```

---

## 🚀 Running the Pipeline

Ensure that the latest export of your Google Form is saved as `form_responses.csv` in the same directory.

### 1. Full Pipeline (Default)
Reruns the entire ingestion, fetching all historical db data and recalculating all features from scratch.
```bash
python data_ingestion.py --mode full --form form_responses.csv --output merged_comfort_dataset.csv
```

### 2. Incremental Update
Fetches only the rows that have been appended since the last run. Much faster for daily CRON jobs.
```bash
python data_ingestion.py --mode incremental
```

---

## 📥 Input Factors (Data Sources)

The pipeline integrates three primary data streams:

1. **IoT Environmental Sensor (`aqi_readings` table in NeonDB)**:
   - Evaluated continuously (downsampled to 1-minute averages).
   - **Inputs:** `temperature` (Air Temp, $T_a$), `humidity` (Relative Humidity, $RH$), `co2`, `pm2_5`, `final_aqi`, `window_status`.
   
2. **IoT Energy Sensor (`sensor_readings` table in NeonDB)**:
   - Tracks the energy consumption of the HVAC/Room components.
   - **Inputs:** `energy_kwh` (Lifetime kWh), `voltage`, `current`.

3. **User Survey (Google Form CSV)**:
   - Ground-truth labels and localized conditions reported by the occupants.
   - **Inputs:** `Timestamp`, `Date of birth`, `Gender`. 
   - **Preferences:** Thermal Sensation Vote (AMV scale -3 to +3), Comfort Level, Delta Temp Preference, Delta Fan Preference.
   - **Categorical conditions:** Inner/Upper/Lower garments, Footwear, Activity Type, Perceived Fan Velocity (0-4 scale).

*(Note: The script dynamically utilizes partial substring matching to parse Google Form columns, making it robust against minor form edits.)*

---

## ⚙️ Feature Engineering & Transformations

To generate the final supervised dataset, the pipeline performs sophisticated domain-specific engineering, primarily focusing on calculating **Predicted Mean Vote (PMV)** and **Adaptive PMV (aPMV)**.

### 1. Clothing Insulation ($I_{cl}$ / CLO)
The script iterates across 4 categorical columns (Inner layers, Upper body, Lower body, Footwear). It parses the comma-separated options checkboxes selected by the user, looks them up against a standardized ASHRAE CLO dictionary, and sums them together. It forces a bare-minimum CLO of `0.10`.

### 2. Metabolic Rate ($M_{met}$ / MET)
Parses the currently reported activity statuses (e.g., "typing", "seating", "walking"). Takes the maximum MET value from the user's selected activities based on the `MET_MAP`.

### 3. Air Velocity ($v_a$)
Instead of an arbitrary default, the script derives the real-time air velocity from the user's perception of the fan speed (0=Fan Off -> 4=AC Blowing), translating this 0-4 scale linearly into $0.10$ m/s to $0.60$ m/s.

### 4. Timeseries Alignment (As-Of Merge)
User responses specify an exact Form Submission Timestamp. The pipeline performs a `pandas.merge_asof` operation to search backwards/forwards in time within a maximum **5-minute tolerance** window to pair the human response with the closest corresponding 1-minute IoT database sensor bucket.

### 5. Power & Demographics
- **Power (kW & W):** Calculated instantaneously using $P = V \times I$.
- **Energy Delta:** Calculates the differential increase in total `energy_kwh` per minute.
- **Age:** Derived dynamically from the user's `Date of birth` relative to today.

### 6. Standard PMV & PPD Calculation
Executes an iterative approximation sequence to solve Fanger's Standard ISO 7730 Comfort Equation. 
- Computes $f_{cl}$ (clothing area factor) and $h_c$ (convective heat transfer coefficient).
- Calculates standard PMV and Predicted Percentage of Dissatisfied (PPD).
- Assumes Mean Radiant Temperature ($T_r$) is roughly equivalent to internal Air Temperature ($T_a$).

### 7. Adaptive PMV (aPMV)
Utilizes the "Yao et al. (2009)" adaptive comfort model, which compensates for warm-humid climate habituation. 
- The script uses a rolling window (default `N=30`) of recent human responses to define the adaptive calibration coefficient ($\lambda$). 
- Calculates $aPMV = \frac{PMV}{1 + \lambda \times PMV}$.
- Generates the boolean `in_comfort_zone` variable if $|aPMV| \le 0.5$.

### 8. Target / Errors
Computes `pmv_error` ($AMV - PMV_{predicted}$) to highlight discrepancies between the rigid Fanger model and the actual human thermal sensation voted that day mapping directly to feature matrices used for gradient boosting predictors.