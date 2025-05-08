import os
import sys
from flask import Flask, request, render_template_string, redirect, url_for, flash, jsonify
import pymongo
from pymongo.errors import ConnectionFailure, ConfigurationError
from datetime import datetime, timedelta, timezone
import pytz # For timezone conversion
import plotly
import plotly.graph_objs as go
import pandas as pd
import numpy as np # For NaN handling
import json
from dotenv import load_dotenv # Import load_dotenv
import math # For ceiling/floor
import hmac # For secure password comparison

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration ---
MONGO_URI = os.environ.get("MONGO_URI")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
CLEAR_DATA_PASSWORD = os.environ.get("CLEAR_DATA_PASSWORD")

if not MONGO_URI:
    print("FATAL ERROR: MONGO_URI not found in environment variables or .env file. Exiting.", file=sys.stderr)
    sys.exit(1)
if not FLASK_SECRET_KEY:
    print("Warning: FLASK_SECRET_KEY not found in environment or .env. Using default (insecure).", file=sys.stderr)
    FLASK_SECRET_KEY = "default_dev_secret_key_highly_insecure"
if not CLEAR_DATA_PASSWORD:
    print("FATAL ERROR: CLEAR_DATA_PASSWORD not found in environment variables or .env file. Exiting.", file=sys.stderr)
    sys.exit(1)

DATABASE_NAME = "Weather"
COLLECTION_NAME = "Temp"
NYC_TIMEZONE_STR = "America/New_York"
DEFAULT_HOURS = 24
ALLOWED_HOURS = [1, 3, 6, 12, 24, 48, 72]
Y_AXIS_PADDING = 2
ROLLING_AVG_WINDOW = '5min'

# --- Flask App Setup ---
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# --- Database Connection ---
mongo_client = None
db = None
collection = None

def connect_db():
    global mongo_client, db, collection
    if collection is None:
        try:
            if mongo_client is None:
                print(f"Connecting to MongoDB Atlas...")
                mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
                mongo_client.admin.command('ping')
            db = mongo_client[DATABASE_NAME]
            collection = db[COLLECTION_NAME]
            print(f"Successfully connected to MongoDB - DB: '{DATABASE_NAME}', Collection: '{COLLECTION_NAME}'")
        except (ConfigurationError, ConnectionFailure) as e:
            print(f"MongoDB Connection Error: {e}", file=sys.stderr)
            mongo_client = None
            collection = None
        except Exception as e:
            print(f"An unexpected error occurred during MongoDB connection: {e}", file=sys.stderr)
            mongo_client = None
            collection = None
    return collection

# --- Timezone Handling ---
try:
    NYC_TZ = pytz.timezone(NYC_TIMEZONE_STR)
except pytz.exceptions.UnknownTimeZoneError:
    print(f"Error: Unknown timezone '{NYC_TIMEZONE_STR}'. Exiting.", file=sys.stderr)
    sys.exit(1)

def convert_to_nyc_time(utc_dt):
    if not isinstance(utc_dt, datetime):
        return None
    if utc_dt.tzinfo is None:
        utc_dt = pytz.utc.localize(utc_dt)
    else:
        utc_dt = utc_dt.astimezone(pytz.utc)
    return utc_dt.astimezone(NYC_TZ)

# --- HTML Template ---
HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Weather Station Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 20px; background-color: #eef2f7; color: #333; }
        .container { max-width: 1140px; }
        .card { margin-bottom: 25px; box-shadow: 0 4px 8px rgba(0,0,0,0.08); border: none; border-radius: 0.75rem; background-color: #fff; }
        .card-header { background-color: #4a90e2; color: white; font-weight: 600; border-top-left-radius: 0.75rem; border-top-right-radius: 0.75rem; padding: 0.8rem 1.2rem; display: flex; align-items: center; gap: 8px; }
        .card-header .bi { font-size: 1.1rem; vertical-align: middle; }
        .stat-value { font-size: 1.8rem; font-weight: 700; color: #2c3e50; }
        .timestamp { font-size: 0.85rem; color: #7f8c8d; }
        #plotly-graph { height: 450px; border-radius: 0 0 0.75rem 0.75rem; }
        .alert { margin-top: 15px; border-radius: 0.5rem; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .btn { border-radius: 0.4rem; padding: 0.5rem 1rem; font-weight: 500; }
        .btn-danger { background-color: #e74c3c; border-color: #e74c3c; }
        .btn-danger:hover { background-color: #c0392b; border-color: #c0392b; }
        .form-control, .form-select, .form-check-input { border-radius: 0.4rem; border: 1px solid #ced4da; }
        .controls-row {
            display: flex;
            align-items: center;
            margin-bottom: 1.5rem;
            flex-wrap: wrap; 
            gap: 15px; 
            background-color: #fff;
            padding: 1rem 1.5rem;
            border-radius: 0.75rem;
            box-shadow: 0 4px 8px rgba(0,0,0,0.08);
        }
        .controls-row label, .controls-row .data-label { font-weight: 500; margin-bottom: 0; white-space: nowrap; }
        .controls-row .data-value { font-weight: bold; color: #2c3e50; white-space: nowrap; }
        .form-check-label { font-weight: normal !important; }
        h1 { color: #34495e; font-weight: 600; }
        .control-item { display: flex; align-items: center; gap: 0.5rem; } 
        .time-suffix { font-size: 0.9em; color: #555; margin-left: 0.25rem;}
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4 text-center">Weather Station Dashboard</h1>

        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                    <i class="bi {% if category == 'danger' or category == 'warning' %}bi-exclamation-triangle-fill{% elif category == 'success' %}bi-check-circle-fill{% else %}bi-info-circle-fill{% endif %} me-2"></i>
                    {{ message }}
                    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <div class="controls-row">
            <form id="settingsForm" method="get" action="{{ url_for('index') }}" class="d-flex align-items-center flex-wrap gap-3 me-auto">
                <div class="control-item">
                    <label for="hoursSelect" class="form-label"><i class="bi bi-clock me-1"></i>Show Last:</label>
                    <select class="form-select" id="hoursSelect" name="hours" style="width: auto;" onchange="document.getElementById('settingsForm').submit()">
                        {% for h in allowed_hours %}
                            <option value="{{ h }}" {% if h == selected_hours %}selected{% endif %}>{{ h }} Hour{% if h != 1 %}s{% endif %}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-check form-switch control-item">
                    <input class="form-check-input" type="checkbox" role="switch" id="autoRefreshSwitch" name="autorefresh" value="true" {% if autorefresh_enabled %}checked{% endif %} onchange="document.getElementById('settingsForm').submit()">
                    <label class="form-check-label ms-1" for="autoRefreshSwitch"><i class="bi bi-arrow-repeat me-1"></i>Auto-refresh</label>
                </div>
                <noscript><button type="submit" class="btn btn-primary btn-sm">Update View</button></noscript>
            </form>
            <div class="control-item">
                <span class="data-label"><i class="bi bi-activity me-1"></i>Current 5-Min Avg:</span>
                <span class="data-value">{% if current_rolling_avg %}{{ current_rolling_avg }} °F{% else %}N/A{% endif %}</span>
            </div>
            <div class="control-item">
                <span class="data-label"><i class="bi bi-graph-up-arrow me-1"></i>Highest 5-Min Avg ({{selected_hours}}h):</span>
                <span class="data-value">
                    {% if highest_rolling_avg_period %}
                        {{ highest_rolling_avg_period }} °F
                        {% if highest_rolling_avg_time %}
                            <span class="time-suffix">at {{ highest_rolling_avg_time }}</span>
                        {% endif %}
                    {% else %}
                        N/A
                    {% endif %}
                </span>
            </div>
        </div>

        <div class="row">
            <div class="col-lg-4 col-md-6">
                <div class="card text-center h-100">
                    <div class="card-header"><i class="bi bi-thermometer-half"></i>Latest Reading</div>
                    <div class="card-body d-flex flex-column justify-content-center">
                        {% if latest_reading %}
                            <p class="stat-value mb-1">{{ latest_reading.temp_f }} °F</p>
                            <p class="timestamp mt-1">Recorded: {{ latest_reading.time_nyc }} (NYC)</p>
                        {% else %}
                            <p class="text-muted my-auto">No recent data available.</p>
                        {% endif %}
                    </div>
                </div>
            </div>

            <div class="col-lg-8 col-md-6">
                <div class="card h-100">
                    <div class="card-header"><i class="bi bi-graph-up"></i>Last {{ selected_hours }} Hour{% if selected_hours != 1 %}s{% endif %} Statistics (°F)</div>
                    <div class="card-body d-flex align-items-center">
                        {% if stats %}
                        <div class="row text-center w-100">
                            <div class="col">
                                <strong>Min:</strong><br><span class="stat-value">{{ stats.min_f }}</span>
                            </div>
                            <div class="col">
                                <strong>Avg:</strong><br><span class="stat-value">{{ stats.avg_f }}</span>
                            </div>
                            <div class="col">
                                <strong>Max:</strong><br><span class="stat-value">{{ stats.max_f }}</span>
                            </div>
                        </div>
                        {% else %}
                            <p class="text-muted mx-auto my-auto">Not enough data for statistics.</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header"><i class="bi bi-bar-chart-line-fill"></i>Temperature Trend (Last {{ selected_hours }} Hour{% if selected_hours != 1 %}s{% endif %})</div>
            <div class="card-body p-2">
                <div id="plotly-graph"></div>
            </div>
        </div>

        <div class="card">
            <div class="card-header"><i class="bi bi-database-fill-gear"></i>Data Management</div>
            <div class="card-body">
                <form action="{{ url_for('delete_old_data', hours=selected_hours, autorefresh=request.args.get('autorefresh', 'false')) }}" method="post">
                    <div class="mb-3">
                        <label for="days_old" class="form-label">Delete data older than (days):</label>
                        <input type="number" class="form-control" id="days_old" name="days_old" min="0" value="30" required>
                    </div>
                    <div class="mb-3">
                        <label for="clear_data_password" class="form-label">Enter Password to Delete:</label>
                        <input type="password" class="form-control" id="clear_data_password" name="clear_data_password" required>
                    </div>
                    <button type="submit" class="btn btn-danger" onclick="return confirm('Are you sure you want to delete data? This cannot be undone.');">
                        <i class="bi bi-trash-fill me-1"></i> Delete Data
                    </button>
                </form>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>
    <script>
        var graphData = {{ graph_json | safe }};
        if (graphData && graphData.data && graphData.data.length > 0) {
            var layout = graphData.layout || {};
            layout.autosize = true; 
            Plotly.newPlot('plotly-graph', graphData.data, layout, {responsive: true});
            window.addEventListener('resize', function() {
                Plotly.Plots.resize('plotly-graph');
            });
        } else {
            document.getElementById('plotly-graph').innerHTML = '<p class="text-center text-muted p-5">No data available to display graph for the selected period.</p>';
        }

        const autoRefreshEnabled = {{ autorefresh_enabled | tojson }};
        const currentPageTimestampISO = "{{ latest_doc_timestamp_iso | safe }}";
        let refreshIntervalId = null;

        async function checkForNewData() {
            if (!autoRefreshEnabled) return;
            // console.log("Checking for new data...", new Date().toLocaleTimeString());
            try {
                const response = await fetch("{{ url_for('check_latest_data_timestamp') }}");
                if (!response.ok) {
                    console.error("Failed to check for new data, status:", response.status);
                    return;
                }
                const data = await response.json();
                if (data.error) {
                    console.error("Error from server:", data.error);
                } else if (data.latest_timestamp_utc_iso && currentPageTimestampISO && data.latest_timestamp_utc_iso > currentPageTimestampISO) {
                    console.log("New data found! Reloading page.");
                    const params = new URLSearchParams(window.location.search);
                    if (autoRefreshEnabled) { 
                        params.set('autorefresh', 'true');
                    }
                    const hoursSelect = document.getElementById('hoursSelect');
                    if (hoursSelect) {
                         params.set('hours', hoursSelect.value);
                    }
                    window.location.search = params.toString();
                }
            } catch (error) {
                console.error("Error during fetch for new data:", error);
            }
        }

        if (autoRefreshEnabled) {
            console.log("Auto-refresh enabled. Checking every 60 seconds. Current page data timestamp:", currentPageTimestampISO);
            refreshIntervalId = setInterval(checkForNewData, 60000);
        }
    </script>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/')
def index():
    coll = connect_db()
    if coll is None:
        flash("Database connection failed. Please check server logs.", "danger")
        return render_template_string(HTML_TEMPLATE, graph_json={}, latest_reading=None, stats=None,
                                      selected_hours=DEFAULT_HOURS, allowed_hours=ALLOWED_HOURS,
                                      autorefresh_enabled=False, latest_doc_timestamp_iso=None,
                                      current_rolling_avg=None, highest_rolling_avg_period=None,
                                      highest_rolling_avg_time=None) # Added new var

    try:
        hours_to_show = int(request.args.get('hours', DEFAULT_HOURS))
        if hours_to_show not in ALLOWED_HOURS:
            flash(f"Invalid time range. Showing default {DEFAULT_HOURS} hours.", "warning")
            hours_to_show = DEFAULT_HOURS
    except ValueError:
        flash(f"Invalid time range format. Showing default {DEFAULT_HOURS} hours.", "warning")
        hours_to_show = DEFAULT_HOURS

    autorefresh_enabled = request.args.get('autorefresh', 'false').lower() == 'true'

    now_utc = datetime.now(timezone.utc)
    start_time_utc = now_utc - timedelta(hours=hours_to_show)

    latest_reading_data = None
    stats_data = None
    graph_json = {}
    y_axis_range = None
    latest_doc_timestamp_iso = None
    current_rolling_avg_val = None
    highest_rolling_avg_period_val = None
    highest_rolling_avg_time_val = None # For storing the formatted time string

    try:
        latest_doc_for_timestamp = coll.find_one(sort=[("timestamp", pymongo.DESCENDING)])
        if latest_doc_for_timestamp and 'timestamp' in latest_doc_for_timestamp:
            latest_doc_timestamp_iso = latest_doc_for_timestamp['timestamp'].isoformat()

        latest_doc = latest_doc_for_timestamp
        if latest_doc:
            latest_time_nyc = convert_to_nyc_time(latest_doc.get('timestamp'))
            temp_f_latest = latest_doc.get('average_temp_f')
            latest_reading_data = {
                "temp_f": f"{temp_f_latest:.2f}" if isinstance(temp_f_latest, (int, float)) else "N/A",
                "time_nyc": latest_time_nyc.strftime('%I:%M:%S %p') if latest_time_nyc else "N/A", # Already 12hr
                "timestamp_utc_iso": latest_doc.get('timestamp').isoformat() if latest_doc.get('timestamp') else None
            }

        cursor = coll.find(
            {"timestamp": {"$gte": start_time_utc}},
            {"timestamp": 1, "average_temp_f": 1, "_id": 0}
        ).sort("timestamp", pymongo.ASCENDING)
        data = list(cursor)

        if data:
            df = pd.DataFrame(data)
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            df.dropna(subset=['timestamp'], inplace=True)
            df['average_temp_f'] = pd.to_numeric(df['average_temp_f'], errors='coerce')
            df.dropna(subset=['average_temp_f'], inplace=True)

            if not df.empty:
                df['time_nyc'] = df['timestamp'].apply(lambda x: convert_to_nyc_time(x))
                df.dropna(subset=['time_nyc'], inplace=True)
                df.sort_values('time_nyc', inplace=True)

                if not df.empty:
                    min_temp = df['average_temp_f'].min()
                    max_temp = df['average_temp_f'].max()
                    avg_temp = df['average_temp_f'].mean()
                    stats_data = {
                        "min_f": f"{min_temp:.2f}" if pd.notna(min_temp) else "N/A",
                        "avg_f": f"{avg_temp:.2f}" if pd.notna(avg_temp) else "N/A",
                        "max_f": f"{max_temp:.2f}" if pd.notna(max_temp) else "N/A"
                    }

                    df_for_rolling = df.set_index('time_nyc')
                    if not df_for_rolling.index.is_monotonic_increasing:
                        df_for_rolling = df_for_rolling.sort_index()
                    
                    rolling_series = df_for_rolling['average_temp_f'].rolling(window=ROLLING_AVG_WINDOW, min_periods=1).mean()
                    
                    non_na_rolling_series = rolling_series.dropna()
                    if not non_na_rolling_series.empty:
                        last_actual_time_nyc = df['time_nyc'].iloc[-1]
                        if last_actual_time_nyc in rolling_series.index: # Check if the time exists in the rolling series index
                            current_val_raw = rolling_series.loc[last_actual_time_nyc]
                            if pd.notna(current_val_raw):
                                current_rolling_avg_val = f"{current_val_raw:.2f}"
                        
                        max_roll_raw = non_na_rolling_series.max() # Use non_na_rolling_series for max
                        if pd.notna(max_roll_raw):
                            highest_rolling_avg_period_val = f"{max_roll_raw:.2f}"
                            # Get the timestamp of the max value
                            time_of_max_roll = non_na_rolling_series.idxmax() # idxmax on non_na series
                            if isinstance(time_of_max_roll, pd.Timestamp): # Ensure it's a timestamp object
                                highest_rolling_avg_time_val = time_of_max_roll.strftime('%I:%M %p')
                    
                    if pd.notna(min_temp) and pd.notna(max_temp):
                        if min_temp == max_temp:
                            y_min_calc = math.floor(min_temp - max(Y_AXIS_PADDING, 1))
                            y_max_calc = math.ceil(max_temp + max(Y_AXIS_PADDING, 1))
                        else:
                            y_min_calc = math.floor(min_temp - Y_AXIS_PADDING)
                            y_max_calc = math.ceil(max_temp + Y_AXIS_PADDING)
                        if y_min_calc >= y_max_calc:
                            y_min_calc = math.floor(y_max_calc - 1) if y_max_calc > 0 else -1
                            y_max_calc = math.ceil(y_min_calc + 1) if y_min_calc < 100 else y_min_calc + 1
                        y_axis_range = [y_min_calc, y_max_calc]
                    else:
                        y_axis_range = None

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df['time_nyc'].tolist(), y=df['average_temp_f'].tolist(),
                        mode='lines', name='Temperature (°F)',
                        line=dict(color='#4a90e2', width=2.5),
                        fill='tozeroy', fillcolor='rgba(74, 144, 226, 0.1)'
                    ))
                    fig.update_layout(
                        xaxis_title=None, yaxis_title='°F',
                        yaxis_range=y_axis_range, 
                        margin=dict(l=50, r=20, t=20, b=30), 
                        hovermode="x unified", template="plotly_white",
                        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                        xaxis=dict(
                            showgrid=True, 
                            gridcolor='#f0f0f0',
                            tickformat='%I:%M %p' # 12-hour format for x-axis ticks
                        ),
                        yaxis=dict(gridcolor='#e0e0e0'),
                        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor='rgba(255,255,255,0.7)')
                    )
                    graph_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
                else:
                    print(f"No valid data after timezone conversion/sort for the last {hours_to_show} hours.")
            else:
                print(f"No valid data after cleaning for the last {hours_to_show} hours.")
        else:
            print(f"No data found in the last {hours_to_show} hours.")

    except Exception as e:
        print(f"Error fetching or processing data in index route: {e}", file=sys.stderr)
        flash(f"Error fetching or processing data: {e}", "warning")
        graph_json = {} 

    return render_template_string(
        HTML_TEMPLATE,
        graph_json=graph_json,
        latest_reading=latest_reading_data,
        stats=stats_data,
        selected_hours=hours_to_show,
        allowed_hours=ALLOWED_HOURS,
        autorefresh_enabled=autorefresh_enabled,
        latest_doc_timestamp_iso=latest_doc_timestamp_iso,
        current_rolling_avg=current_rolling_avg_val,
        highest_rolling_avg_period=highest_rolling_avg_period_val,
        highest_rolling_avg_time=highest_rolling_avg_time_val # Pass the formatted time
    )

@app.route('/check_latest_data_timestamp')
def check_latest_data_timestamp():
    coll = connect_db()
    if coll is None:
        return jsonify({"error": "Database connection failed"}), 500
    try:
        latest_doc = coll.find_one(sort=[("timestamp", pymongo.DESCENDING)], projection={"timestamp": 1})
        if latest_doc and 'timestamp' in latest_doc:
            return jsonify({"latest_timestamp_utc_iso": latest_doc['timestamp'].isoformat()})
        else:
            return jsonify({"latest_timestamp_utc_iso": None})
    except Exception as e:
        print(f"Error in /check_latest_data_timestamp: {e}", file=sys.stderr)
        return jsonify({"error": str(e)}), 500

@app.route('/delete_old_data', methods=['POST'])
def delete_old_data():
    coll = connect_db()
    current_hours = request.args.get('hours', DEFAULT_HOURS)
    autorefresh_status = request.args.get('autorefresh', 'false')
    redirect_url = url_for('index', hours=current_hours, autorefresh=autorefresh_status)

    if coll is None:
        flash("Database connection failed. Cannot delete data.", "danger")
        return redirect(redirect_url)

    submitted_password = request.form.get('clear_data_password')
    stored_password = CLEAR_DATA_PASSWORD

    if not submitted_password or not stored_password:
        flash("Password configuration error or missing password.", "danger")
        return redirect(redirect_url)
    if not hmac.compare_digest(submitted_password.encode('utf-8'), stored_password.encode('utf-8')):
        flash("Invalid password. Data not deleted.", "danger")
        return redirect(redirect_url)

    try:
        days_old_str = request.form.get('days_old')
        if days_old_str is None:
            flash("Number of days not provided.", "danger")
            return redirect(redirect_url)
            
        days_old = int(days_old_str)

        if days_old < 0:
            flash("Please provide a non-negative number of days.", "warning")
            return redirect(redirect_url)

        if days_old == 0:
            # Delete all data
            print(f"Attempting to delete ALL documents...")
            result = coll.delete_many({}) # Empty filter deletes all
            deleted_count = result.deleted_count
            flash(f"Successfully deleted all {deleted_count} record(s).", "success")
            print(f"Deletion of all data successful. {deleted_count} records removed.")
        else:
            # Delete data older than 'days_old'
            cutoff_date_utc = datetime.now(timezone.utc) - timedelta(days=days_old)
            print(f"Attempting to delete documents older than {days_old} days (before {cutoff_date_utc.strftime('%Y-%m-%d %H:%M:%S UTC')})...")
            result = coll.delete_many({"timestamp": {"$lt": cutoff_date_utc}})
            deleted_count = result.deleted_count
            flash(f"Successfully deleted {deleted_count} old record(s) older than {days_old} days.", "success")
            print(f"Deletion successful. {deleted_count} records removed.")

    except ValueError:
        flash("Invalid number of days provided. Please enter a whole number.", "danger")
    except Exception as e:
        print(f"Error deleting data: {e}", file=sys.stderr)
        flash(f"An error occurred while deleting data: {e}", "danger")
    return redirect(redirect_url)

if __name__ == '__main__':
    print("Starting Flask development server...")
    if connect_db() is None:
        print("\n--- CRITICAL: Cannot start Flask server due to DB connection failure at startup ---", file=sys.stderr)
        sys.exit(1)
    app.run(debug=True, host='127.0.0.1', port=5000, use_reloader=False)