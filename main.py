import os
import sys
from flask import Flask, request, render_template_string, redirect, url_for, flash
import pymongo
from pymongo.errors import ConnectionFailure, ConfigurationError
from datetime import datetime, timedelta, timezone
import pytz # For timezone conversion
import plotly
import plotly.graph_objs as go
import pandas as pd
import json
from dotenv import load_dotenv # Import load_dotenv
import math # For ceiling/floor

# --- Load Environment Variables ---
# Load variables from .env file into environment variables
load_dotenv()

# --- Configuration ---
# Read configuration from environment variables
MONGO_URI = os.environ.get("MONGO_URI")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")

# Check if essential variables are loaded
if not MONGO_URI:
    print("FATAL ERROR: MONGO_URI not found in environment variables or .env file. Exiting.", file=sys.stderr)
    sys.exit(1)
if not FLASK_SECRET_KEY:
     print("Warning: FLASK_SECRET_KEY not found in environment or .env. Using default (insecure).", file=sys.stderr)
     FLASK_SECRET_KEY = "default_dev_secret_key_highly_insecure" # Fallback only for immediate running


DATABASE_NAME = "Weather" # Can be env vars too
COLLECTION_NAME = "Temp"
NYC_TIMEZONE_STR = "America/New_York"
DEFAULT_HOURS = 24
# Updated ALLOWED_HOURS list
ALLOWED_HOURS = [1, 3, 6, 12, 24, 48, 72] # Allowed time ranges in hours
Y_AXIS_PADDING = 2 # Degrees F padding for top/bottom of graph

# --- Flask App Setup ---
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY # Use secret key from environment

# --- Database Connection ---
mongo_client = None
db = None
collection = None

def connect_db():
    """Establishes connection to MongoDB. Returns collection object or None."""
    global mongo_client, db, collection
    # Only attempt connection if collection object is not already available
    if collection is None:
        try:
            if mongo_client is None: # Avoid reconnecting if client exists but collection failed before
                 print(f"Connecting to MongoDB Atlas...")
                 mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
            # Verify connection with ping
            mongo_client.admin.command('ping')
            db = mongo_client[DATABASE_NAME]
            collection = db[COLLECTION_NAME]
            print(f"Successfully connected to MongoDB - DB: '{DATABASE_NAME}', Collection: '{COLLECTION_NAME}'")
        except ConfigurationError as e:
            print(f"MongoDB Configuration Error: {e}", file=sys.stderr)
            print("Ensure 'pymongo[srv]' is installed ('pip install pymongo[srv]'). Check URI format.", file=sys.stderr)
            mongo_client = None # Reset client on config error
            collection = None
        except ConnectionFailure as e:
            print(f"Error connecting to MongoDB: {e}", file=sys.stderr)
            print("Check URI, credentials, network (firewalls!), and Atlas cluster status/IP whitelist.", file=sys.stderr)
            # Keep client object but set collection to None, maybe retry later?
            collection = None
        except Exception as e:
            print(f"An unexpected error occurred during MongoDB connection: {e}", file=sys.stderr)
            mongo_client = None # Reset client on unexpected error
            collection = None
    return collection

# --- Timezone Handling ---
try:
    NYC_TZ = pytz.timezone(NYC_TIMEZONE_STR)
except pytz.exceptions.UnknownTimeZoneError:
    print(f"Error: Unknown timezone '{NYC_TIMEZONE_STR}'. Exiting.", file=sys.stderr)
    sys.exit(1)

def convert_to_nyc_time(utc_dt):
    """Converts a UTC datetime object to NYC time."""
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
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            padding: 20px;
            background-color: #eef2f7; /* Lighter background */
            color: #333;
        }
        .container {
            max-width: 1140px; /* Limit max width */
        }
        .card {
            margin-bottom: 25px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.08); /* Softer shadow */
            border: none; /* Remove default border */
            border-radius: 0.75rem; /* Slightly more rounded */
            background-color: #fff;
        }
        .card-header {
            background-color: #4a90e2; /* Softer blue */
            color: white;
            font-weight: 600; /* Semi-bold */
            border-top-left-radius: 0.75rem;
            border-top-right-radius: 0.75rem;
            padding: 0.8rem 1.2rem;
            display: flex;
            align-items: center;
            gap: 8px; /* Space between icon and text */
        }
        .card-header .bi { /* Icon styling */
            font-size: 1.1rem;
            vertical-align: middle;
        }
        .stat-value {
            font-size: 1.8rem; /* Larger stat value */
            font-weight: 700; /* Bold */
            color: #2c3e50; /* Darker color for stats */
        }
        .timestamp {
            font-size: 0.85rem;
            color: #7f8c8d; /* Muted grey */
        }
        #plotly-graph {
            height: 450px;
            border-radius: 0 0 0.75rem 0.75rem; /* Match card rounding */
        }
        .alert {
            margin-top: 15px;
            border-radius: 0.5rem;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        .btn {
            border-radius: 0.4rem;
            padding: 0.5rem 1rem;
            font-weight: 500;
        }
        .btn-danger {
             background-color: #e74c3c;
             border-color: #e74c3c;
        }
        .btn-danger:hover {
             background-color: #c0392b;
             border-color: #c0392b;
        }
        .form-control, .form-select {
            border-radius: 0.4rem;
            border: 1px solid #ced4da;
        }
        .controls-row {
            display: flex;
            /* justify-content: space-between; */ /* Let items align left */
            align-items: center;
            margin-bottom: 1.5rem; /* More space below controls */
            flex-wrap: wrap;
            gap: 15px; /* More gap */
            background-color: #fff; /* White background for controls */
            padding: 1rem 1.5rem;
            border-radius: 0.75rem;
             box-shadow: 0 4px 8px rgba(0,0,0,0.08);
        }
        .controls-row label {
            font-weight: 500;
        }
        h1 {
            color: #34495e; /* Darker heading color */
            font-weight: 600;
        }
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
             <form id="hoursForm" method="get" action="{{ url_for('index') }}" class="d-flex align-items-center">
                 <label for="hoursSelect" class="form-label me-2 mb-0"><i class="bi bi-clock me-1"></i>Show Last:</label>
                 <select class="form-select me-2" id="hoursSelect" name="hours" style="width: auto;" onchange="this.form.submit()">
                    {% for h in allowed_hours %}
                        <option value="{{ h }}" {% if h == selected_hours %}selected{% endif %}>{{ h }} Hour{% if h != 1 %}s{% endif %}</option> {# Handle plural 'Hours' #}
                    {% endfor %}
                 </select>
                 <noscript><button type="submit" class="btn btn-primary btn-sm">Update</button></noscript>
                 </form>
             </div>


        <div class="row">
            <div class="col-lg-4 col-md-6">
                <div class="card text-center h-100"> <div class="card-header"><i class="bi bi-thermometer-half"></i>Latest Reading</div>
                    <div class="card-body d-flex flex-column justify-content-center"> {% if latest_reading %}
                            <p class="stat-value mb-1">{{ latest_reading.temp_f }} °F</p> {# Updated to .2f formatting from backend #}
                            <p class="timestamp mt-1">Recorded: {{ latest_reading.time_nyc }} (NYC)</p>
                        {% else %}
                            <p class="text-muted my-auto">No recent data available.</p> {% endif %}
                    </div>
                </div>
            </div>

            <div class="col-lg-8 col-md-6">
                <div class="card h-100">
                    <div class="card-header"><i class="bi bi-graph-up"></i>Last {{ selected_hours }} Hour{% if selected_hours != 1 %}s{% endif %} Statistics (°F)</div> {# Handle plural 'Hours' #}
                    <div class="card-body d-flex align-items-center"> {% if stats %}
                        <div class="row text-center w-100"> <div class="col">
                                <strong>Min:</strong><br><span class="stat-value">{{ stats.min_f }}</span> {# Updated to .2f formatting from backend #}
                            </div>
                            <div class="col">
                                <strong>Avg:</strong><br><span class="stat-value">{{ stats.avg_f }}</span> {# Updated to .2f formatting from backend #}
                            </div>
                            <div class="col">
                                <strong>Max:</strong><br><span class="stat-value">{{ stats.max_f }}</span> {# Updated to .2f formatting from backend #}
                            </div>
                        </div>
                        {% else %}
                            <p class="text-muted mx-auto my-auto">Not enough data for statistics.</p> {% endif %}
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <div class="card-header"><i class="bi bi-bar-chart-line-fill"></i>Temperature Trend (Last {{ selected_hours }} Hour{% if selected_hours != 1 %}s{% endif %})</div> {# Handle plural 'Hours' #}
            <div class="card-body p-2"> <div id="plotly-graph"></div>
            </div>
        </div>

        <div class="card">
            <div class="card-header"><i class="bi bi-database-fill-gear"></i>Data Management</div>
            <div class="card-body">
                <form action="{{ url_for('delete_old_data', hours=selected_hours) }}" method="post"> <div class="mb-3">
                        <label for="days_old" class="form-label">Delete data older than (days):</label>
                        <input type="number" class="form-control" id="days_old" name="days_old" min="1" value="30" required>
                    </div>
                    <button type="submit" class="btn btn-danger" onclick="return confirm('Are you sure you want to delete old data? This cannot be undone.');">
                       <i class="bi bi-trash-fill me-1"></i> Delete Old Data
                    </button>
                </form>
            </div>
        </div>

        </div> <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>
    <script>
        // Embed Plotly graph
        var graphData = {{ graph_json | safe }};
        if (graphData && graphData.data && graphData.data.length > 0) {
             var layout = graphData.layout || {};
             layout.autosize = true; // Make graph responsive
             Plotly.newPlot('plotly-graph', graphData.data, layout, {responsive: true});
             // Add resize listener
             window.addEventListener('resize', function() {
                Plotly.Plots.resize('plotly-graph');
             });
        } else {
            document.getElementById('plotly-graph').innerHTML = '<p class="text-center text-muted p-5">No data available to display graph for the selected period.</p>';
        }

    </script>
</body>
</html>
"""

# --- Flask Routes ---

@app.route('/')
def index():
    """Main dashboard route."""
    coll = connect_db()
    if coll is None:
        flash("Database connection failed. Please check server logs.", "danger")
        return render_template_string(HTML_TEMPLATE, graph_json={}, latest_reading=None, stats=None, selected_hours=DEFAULT_HOURS, allowed_hours=ALLOWED_HOURS)

    # --- Get parameters from request ---
    try:
        hours_to_show = int(request.args.get('hours', DEFAULT_HOURS))
        if hours_to_show not in ALLOWED_HOURS:
            flash(f"Invalid time range. Showing default {DEFAULT_HOURS} hours.", "warning")
            hours_to_show = DEFAULT_HOURS
    except ValueError:
        flash(f"Invalid time range format. Showing default {DEFAULT_HOURS} hours.", "warning")
        hours_to_show = DEFAULT_HOURS

    # --- Fetch data based on selected hours ---
    now_utc = datetime.now(timezone.utc)
    start_time_utc = now_utc - timedelta(hours=hours_to_show)

    latest_reading_data = None
    stats_data = None
    graph_json = {}
    y_axis_range = None # Initialize y-axis range

    try:
        # Fetch latest reading separately first
        latest_doc = coll.find_one(sort=[("timestamp", pymongo.DESCENDING)])
        if latest_doc:
            latest_time_nyc = convert_to_nyc_time(latest_doc.get('timestamp'))
            temp_f_latest = latest_doc.get('average_temp_f')
            latest_reading_data = {
                # Format with TWO decimal places
                "temp_f": f"{temp_f_latest:.2f}" if isinstance(temp_f_latest, (int, float)) else "N/A",
                "time_nyc": latest_time_nyc.strftime('%Y-%m-%d %I:%M:%S %p') if latest_time_nyc else "N/A"
            }

        # Fetch data for the graph and stats
        cursor = coll.find(
            {"timestamp": {"$gte": start_time_utc}},
            {"timestamp": 1, "average_temp_f": 1, "_id": 0}
        ).sort("timestamp", pymongo.ASCENDING)

        data = list(cursor)

        if data:
            df = pd.DataFrame(data)
            # Convert timestamp and drop errors
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            df.dropna(subset=['timestamp'], inplace=True)
            # Convert temp to numeric and drop errors (important for min/max)
            df['average_temp_f'] = pd.to_numeric(df['average_temp_f'], errors='coerce')
            df.dropna(subset=['average_temp_f'], inplace=True)


            if not df.empty:
                df['time_nyc'] = df['timestamp'].apply(convert_to_nyc_time)
                df.dropna(subset=['time_nyc'], inplace=True)

                if not df.empty:
                    # Calculate stats for the selected period (df)
                    min_temp = df['average_temp_f'].min()
                    max_temp = df['average_temp_f'].max()
                    avg_temp = df['average_temp_f'].mean()

                    stats_data = {
                        # Format with TWO decimal places
                        "min_f": f"{min_temp:.2f}",
                        "avg_f": f"{avg_temp:.2f}",
                        "max_f": f"{max_temp:.2f}"
                    }

                    # --- Calculate Y-axis range with padding ---
                    if min_temp == max_temp: # Handle case with only one value or all same values
                        # Use a slightly larger default padding if range is zero
                        y_min = math.floor(min_temp - max(Y_AXIS_PADDING, 1))
                        y_max = math.ceil(max_temp + max(Y_AXIS_PADDING, 1))
                    else:
                        y_min = math.floor(min_temp - Y_AXIS_PADDING)
                        y_max = math.ceil(max_temp + Y_AXIS_PADDING)
                    # Ensure min is not greater than max after padding if range is very small
                    if y_min >= y_max:
                         # Ensure at least a small range, adjust based on magnitude if needed
                        y_min = math.floor(y_max - 1) if y_max > 0 else -1
                        y_max = math.ceil(y_min + 1) if y_min < 100 else y_min + 1 # Avoid huge ranges if near zero

                    y_axis_range = [y_min, y_max]
                    # --- End Y-axis calculation ---


                    # Create Plotly graph for the selected period (df)
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df['time_nyc'].tolist(),         # <--- FIX APPLIED HERE
                        y=df['average_temp_f'].tolist(), # <--- FIX APPLIED HERE
                        mode='lines',
                        name='Temperature (°F)',
                        line=dict(color='#4a90e2', width=2.5),
                        fill='tozeroy',
                        fillcolor='rgba(74, 144, 226, 0.1)'
                    ))
                    fig.update_layout(
                        xaxis_title=None,
                        yaxis_title='°F',
                        yaxis_range=y_axis_range, # Apply calculated range
                        margin=dict(l=40, r=10, t=10, b=20), # Adjusted left margin for potential wider y-axis labels
                        hovermode="x unified",
                        template="plotly_white",
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                        xaxis=dict(showgrid=False),
                        yaxis=dict(gridcolor='#eef2f7')
                    )
                    graph_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
                else:
                    print(f"No valid data remaining after timezone conversion for the last {hours_to_show} hours.")
            else:
                 print(f"No valid data remaining after data cleaning for the last {hours_to_show} hours.")
        else:
             print(f"No data found in the last {hours_to_show} hours.")


    except Exception as e:
        print(f"Error fetching or processing data: {e}", file=sys.stderr)
        flash(f"Error fetching or processing data: {e}", "warning")
        graph_json = {}


    return render_template_string(
        HTML_TEMPLATE,
        graph_json=graph_json,
        latest_reading=latest_reading_data,
        stats=stats_data,
        selected_hours=hours_to_show,
        allowed_hours=ALLOWED_HOURS
    )


@app.route('/delete_old_data', methods=['POST'])
def delete_old_data():
    """Deletes data older than a specified number of days."""
    coll = connect_db()
    current_hours = request.args.get('hours', DEFAULT_HOURS)
    redirect_url = url_for('index', hours=current_hours)

    if coll is None:
        flash("Database connection failed. Cannot delete data.", "danger")
        return redirect(redirect_url)

    try:
        days_old = int(request.form.get('days_old', 0))
        if days_old <= 0:
            flash("Please provide a positive number of days.", "warning")
            return redirect(redirect_url)

        cutoff_date_utc = datetime.now(timezone.utc) - timedelta(days=days_old)
        print(f"Attempting to delete documents older than {days_old} days (before {cutoff_date_utc.strftime('%Y-%m-%d %H:%M:%S UTC')})...")
        result = coll.delete_many({"timestamp": {"$lt": cutoff_date_utc}})
        deleted_count = result.deleted_count
        flash(f"Successfully deleted {deleted_count} old record(s) older than {days_old} days.", "success")
        print(f"Deletion successful. {deleted_count} records removed.")

    except ValueError:
        flash("Invalid number of days provided.", "danger")
    except Exception as e:
        print(f"Error deleting data: {e}", file=sys.stderr)
        flash(f"An error occurred while deleting data: {e}", "danger")

    return redirect(redirect_url)


# --- Run the App ---
if __name__ == '__main__':
    print("Starting Flask development server...")
    if connect_db() is None:
         print("\n--- Cannot start Flask server due to DB connection failure ---", file=sys.stderr)
         sys.exit(1)
    app.run(debug=False, host='127.0.0.1', port=5000, use_reloader=True)