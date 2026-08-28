from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import joblib
import numpy as np
import math
import os
import time
import random
import threading
import datetime
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'IOT_AIApp001'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///plantation.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

# ---------------------------------------------------------
# Simulation & Firebase Abstraction
# ---------------------------------------------------------

class SimulatedFirebaseStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.tank_height_cm = 100
        self.sensor_data = {
            'distance': 30.0,       # Distance to water in tank (cm). Tank level % = (tank_height - distance)/tank_height * 100
            'waterLevel': 150.0,    # Plant pot water level sensor reading
            'humidity': 68.0,       # %
            'lightLevel': 12.0,     # lux
            'soilMoisture': 2800.0, # ADC value (higher = drier in this ADC scale, >2500 needs water)
            'temperature': 28.5,    # °C
            'device1_status': 0,    # Irrigation Pump (0: OFF, 1: ON)
            'device2_status': 0,    # Ventilation Fan (0: OFF, 1: ON)
            'fan_automation': 1,
            'automation_irrigation': 1,
            'automated_alarm': 1,
            'vent_status': 0,
            'buzzer_status': 0
        }
        self.prediction_logs = {}

    def get_sensors(self):
        with self.lock:
            return dict(self.sensor_data)

    def update_sensors(self, updates):
        with self.lock:
            self.sensor_data.update(updates)

    def get_tank_height(self):
        with self.lock:
            return self.tank_height_cm

    def set_tank_height(self, height):
        with self.lock:
            self.tank_height_cm = height

    def add_prediction_log(self, log_dict):
        with self.lock:
            key = f"log_{int(time.time()*1000)}"
            self.prediction_logs[key] = log_dict

    def get_prediction_logs(self):
        with self.lock:
            return dict(self.prediction_logs)

simulated_store = SimulatedFirebaseStore()
USE_SIMULATION = True

# Mock Firebase Reference Class
class MockFirebaseRef:
    def __init__(self, path=''):
        self.path = path.strip('/')

    def get(self):
        if self.path == 'sensors':
            return simulated_store.get_sensors()
        elif self.path == 'prediction_logs':
            return simulated_store.get_prediction_logs()
        elif self.path == 'tank_height_cm':
            return simulated_store.get_tank_height()
        elif self.path == '':
            return {'tank_height_cm': simulated_store.get_tank_height(), 'sensors': simulated_store.get_sensors()}
        return None

    def child(self, name):
        new_path = f"{self.path}/{name}" if self.path else name
        return MockFirebaseRef(new_path)

    def update(self, data):
        if self.path == 'sensors':
            simulated_store.update_sensors(data)
        elif self.path == '' or self.path == 'tank_height_cm':
            if 'tank_height_cm' in data:
                simulated_store.set_tank_height(data['tank_height_cm'])
            else:
                simulated_store.update_sensors(data)
        else:
            simulated_store.update_sensors(data)

    def push(self, log_dict):
        if self.path == 'prediction_logs':
            simulated_store.add_prediction_log(log_dict)

class MockFirebaseDB:
    @staticmethod
    def reference(path='/'):
        return MockFirebaseRef(path)

# Try initializing Firebase, fallback to Mock if credential file is missing
firebase_cred_path = "watering-7b4c7-firebase-adminsdk-ntnzb-ee048fb927.json"
if os.path.exists(firebase_cred_path):
    try:
        import firebase_admin
        from firebase_admin import credentials, db as real_firebase_db
        cred = credentials.Certificate(firebase_cred_path)
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://watering-7b4c7-default-rtdb.firebaseio.com/'
        })
        firebase_db = real_firebase_db
        USE_SIMULATION = False
        print("Connected to Real Firebase Realtime Database.")
    except Exception as e:
        print("Firebase initialization failed, switching to SIMULATION mode:", e)
        firebase_db = MockFirebaseDB
        USE_SIMULATION = True
else:
    print(f"Credentials file '{firebase_cred_path}' not found. Running in SIMULATION mode.")
    firebase_db = MockFirebaseDB
    USE_SIMULATION = True

sensor_ref = firebase_db.reference('/sensors')

# Background Simulator Thread
def run_background_sensor_simulation():
    while True:
        try:
            time.sleep(2.5)
            if not USE_SIMULATION:
                continue

            data = simulated_store.get_sensors()
            temp = float(data.get('temperature', 28.5))
            hum = float(data.get('humidity', 68.0))
            soil = float(data.get('soilMoisture', 2800.0))
            dist = float(data.get('distance', 30.0))
            pot_water = float(data.get('waterLevel', 150.0))
            light = float(data.get('lightLevel', 12.0))
            tank_height = float(simulated_store.get_tank_height())

            # Physical simulation logic:
            # 1. Temperature
            if data.get('device2_status') == 1: # Fan ON
                temp = max(24.0, temp - random.uniform(0.1, 0.3))
            else:
                temp = min(42.0, max(18.0, temp + random.uniform(-0.1, 0.2)))

            # 2. Humidity
            hum = min(95.0, max(30.0, hum + random.uniform(-0.3, 0.3)))

            # 3. Soil Moisture & Water Tank
            if data.get('device1_status') == 1: # Irrigation ON
                soil = max(1400.0, soil - random.uniform(60.0, 120.0)) # Gets wetter (lower ADC)
                dist = min(tank_height, dist + random.uniform(0.4, 0.8)) # Tank empties
                pot_water = min(1200.0, pot_water + random.uniform(15.0, 35.0))
            else:
                soil = min(3800.0, soil + random.uniform(5.0, 15.0)) # Dries out
                pot_water = max(50.0, pot_water - random.uniform(3.0, 10.0))

            light = min(25.0, max(2.0, light + random.uniform(-0.2, 0.2)))

            simulated_store.update_sensors({
                'temperature': round(temp, 1),
                'humidity': round(hum, 1),
                'soilMoisture': round(soil, 1),
                'distance': round(dist, 1),
                'waterLevel': round(pot_water, 1),
                'lightLevel': round(light, 1)
            })
        except Exception as e:
            print("Simulator error:", e)

sim_thread = threading.Thread(target=run_background_sensor_simulation, daemon=True)
sim_thread.start()

# ---------------------------------------------------------
# Database Models
# ---------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    last_fertilizer_date = db.Column(db.String(20))

# ---------------------------------------------------------
# Robust ML Model Loading with Fallbacks
# ---------------------------------------------------------
class FallbackScaler:
    def transform(self, X):
        return X

class FallbackPoly:
    def transform(self, X):
        return X

class FallbackModel:
    def __init__(self, default_val=1.5):
        self.default_val = default_val

    def predict(self, X):
        if hasattr(X, 'shape'):
            return np.full(X.shape[0], self.default_val)
        return np.array([self.default_val])

def safe_load_model(filepath, default_obj):
    if os.path.exists(filepath):
        try:
            return joblib.load(filepath)
        except Exception as e:
            print(f"Warning: Could not unpickle model '{filepath}' ({e}). Using robust fallback.")
            return default_obj
    else:
        print(f"Warning: File '{filepath}' not found. Using robust fallback.")
        return default_obj

model = safe_load_model("models/irrigation_model_best.pkl", FallbackModel(default_val=1.4))
scaler = safe_load_model("models/scaler_best.pkl", FallbackScaler())
poly = safe_load_model("models/poly_best.pkl", FallbackPoly())

fertilizer_rf_model = safe_load_model("models/rf_model.pkl", FallbackModel(default_val=25.0))
fertilizer_gb_model = safe_load_model("models/gb_model.pkl", FallbackModel(default_val=3.0))
fertilizer_scaler = safe_load_model("models/scaler2.pkl", FallbackScaler())

harvest_model = safe_load_model("models/harvest_model.pkl", FallbackModel(default_val=75.0))
harvest_scaler = safe_load_model("models/harvest_scaler.pkl", FallbackScaler())

min_growth_stage, max_growth_stage = safe_load_model("models/growth_stage_range.pkl", (1, 120))
harvest_feature_names = safe_load_model("models/feature_names.pkl", [
    'planting_date', 'growth_stage', 'temperature', 'humidity',
    'light_exposure', 'soil_moisture', 'pesticide_used'
])

# Helper functions
def get_sensor_data():
    """Fetch sensor data from Firebase or Simulated Store"""
    ref = firebase_db.reference('/sensors')
    data = ref.get()
    return data or {}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Prediction Logic
def predict_watering(temperature, humidity, soil_moisture, light_level):
    try:
        input_data = pd.DataFrame([[temperature, humidity, soil_moisture, light_level]],
                                  columns=['Temperature (°C)', 'Humidity (%)', 'Soil Moisture (%)', 'Light Level (lux)'])
        input_data_scaled = scaler.transform(input_data)
        input_data_scaled_df = pd.DataFrame(input_data_scaled, columns=input_data.columns)
        input_data_poly = poly.transform(input_data_scaled_df)
        predicted_water_level = model.predict(input_data_poly)
        return float(predicted_water_level[0])
    except Exception as e:
        print("Fallback predict_watering:", e)
        # Rule-based calculation fallback
        base_req = (temperature * 0.03) + ((soil_moisture / 1000.0) * 0.2) + (light_level * 0.02)
        return float(round(max(0.5, base_req), 2))

def get_prediction_reason(temperature, humidity, soil_moisture, light_level):
    if temperature > 35:
        return "Critical Warning: High temperature detected. Immediate action is required to prevent heat stress!"
    elif temperature > 30:
        return "Alert: Elevated temperature detected. Consider providing shade and increasing watering frequency."
    elif temperature < 15:
        return "Notice: Low temperature detected. Ensure protection against frost and consider using row covers."
    elif humidity < 40:
        return "Alert: Low humidity detected. Increase humidity around plants to prevent blossom drop."
    elif soil_moisture < 20:
        return "Warning: Low soil moisture detected. Increase watering to maintain optimal soil moisture levels."
    elif light_level < 6:
        return "Notice: Insufficient light detected. Ensure plants receive adequate sunlight for healthy growth."
    else:
        return "Conditions are optimal for irrigation. No immediate action required."

def predict_fertilizer(input_data):
    try:
        input_df = pd.DataFrame(input_data)
        input_df = pd.get_dummies(input_df, columns=['Fertilizer Type', 'Soil type', 'Growth stage'], drop_first=True)
        if hasattr(fertilizer_scaler, 'feature_names_in_'):
            cols = fertilizer_scaler.feature_names_in_
            input_df = input_df.reindex(columns=cols, fill_value=0)
        X_scaled = fertilizer_scaler.transform(input_df)
        quantity = fertilizer_rf_model.predict(X_scaled)[0]
        timing = int(round(fertilizer_gb_model.predict(X_scaled)[0]))
        return float(quantity), timing
    except Exception as e:
        print("Fallback predict_fertilizer:", e)
        # Fallback fertilizer prediction
        area = float(input_data.get('Area planted (ha)', [1.0])[0])
        return float(round(15.0 * area, 2)), 2

def predict_harvest(input_data):
    try:
        input_df = pd.DataFrame(input_data, columns=harvest_feature_names)
        input_scaled = harvest_scaler.transform(input_df)
        predicted_days = harvest_model.predict(input_scaled)[0]
        return float(np.clip(predicted_days, 55, 90))
    except Exception as e:
        print("Fallback predict_harvest:", e)
        return 70.0

# Routes
@app.route('/')
def home():
    sensor_data = get_sensor_data()
    return render_template('landing.html', sensor_data=sensor_data, is_simulation=USE_SIMULATION)

@app.route('/information')
@app.route('/about')
def information():
    sensor_data = get_sensor_data()
    return render_template('information.html', sensor_data=sensor_data, is_simulation=USE_SIMULATION)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('Email already exists', 'danger')
        else:
            new_user = User(
                username=username,
                email=email,
                password=generate_password_hash(password, method='pbkdf2:sha256')
            )
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))

    return render_template('auth/register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['username'] = user.username
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')

    return render_template('auth/login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    sensor_data = get_sensor_data()
    return render_template('dashboard.html', sensor_data=sensor_data, is_simulation=USE_SIMULATION)

@app.route("/irrigation", methods=["GET", "POST"])
@login_required
def irrigation():
    predicted_water_level = None
    total_water_requirement = None
    prediction_reason = None
    sensor_data = get_sensor_data()
    count_method = 'direct'
    greenhouse_area = None
    plant_gap = 1.5

    temperature = humidity = soil_moisture = light_level = plant_count = None

    if sensor_data:
        temperature = sensor_data.get('temperature', '')
        humidity = sensor_data.get('humidity', '')
        soil_moisture = sensor_data.get('soilMoisture', '')
        light_level = sensor_data.get('lightLevel', '')

    if request.method == "POST":
        try:
            temperature = float(request.form["temperature"])
            humidity = float(request.form["humidity"])
            soil_moisture = float(request.form["soil_moisture"])
            light_level = float(request.form["light_level"])

            count_method = request.form.get("count_method", "direct")

            if count_method == "area":
                greenhouse_area = float(request.form["greenhouse_area"])
                plant_gap = float(request.form["plant_gap"])
                plant_count = math.floor(greenhouse_area / (plant_gap ** 2))
            else:
                plant_count = int(request.form["plant_count"])

            predicted_water_level = predict_watering(temperature, humidity, soil_moisture, light_level)
            total_water_requirement = predicted_water_level * plant_count
            prediction_reason = get_prediction_reason(temperature, humidity, soil_moisture, light_level)
        except ValueError:
            predicted_water_level = "Invalid input"
            total_water_requirement = "Invalid input"
            prediction_reason = "Invalid input"

    timestamp = datetime.now().isoformat()
    log_ref = firebase_db.reference('/prediction_logs')

    log_ref.push({
        "timestamp": timestamp,
        "temperature": temperature,
        "humidity": humidity,
        "soil_moisture": soil_moisture,
        "light_level": light_level,
        "plant_count": plant_count,
        "per_plant_water": predicted_water_level,
        "total_water": total_water_requirement
    })

    return render_template("irrigation.html",
                           sensor_data=sensor_data,
                           predicted_water_level=predicted_water_level,
                           total_water_requirement=total_water_requirement,
                           prediction_reason=prediction_reason,
                           temperature=temperature,
                           humidity=humidity,
                           soil_moisture=soil_moisture,
                           light_level=light_level,
                           plant_count=plant_count,
                           count_method=count_method,
                           greenhouse_area=greenhouse_area,
                           plant_gap=plant_gap
                           )

@app.route('/sensor_data')
@login_required
def sensor_data():
    return render_template('SensorData.html')

@app.route('/fertilizer', methods=['GET', 'POST'])
@login_required
def fertilizer():
    user = User.query.get(session['user_id'])
    sensor_data = get_sensor_data()
    prediction = None

    if request.method == 'POST':
        if 'submit_fertilizer_form' in request.form:
            try:
                input_data = {
                    'Humidity (%)': [float(request.form['humidity'])],
                    'Temperature (°C)': [float(request.form['temperature'])],
                    'Area planted (ha)': [float(request.form['area_planted'])],
                    'Fertilizer Type': [request.form['fertilizer_type']],
                    'Soil type': [request.form['soil_type']],
                    'Growth stage': [request.form['growth_stage']]
                }

                quantity, weeks = predict_fertilizer(input_data)
                next_date = None

                if user.last_fertilizer_date:
                    date_obj = datetime.strptime(user.last_fertilizer_date, '%Y-%m-%d')
                    next_date = (date_obj + timedelta(weeks=weeks)).strftime('%Y-%m-%d')

                prediction = {
                    'quantity': round(quantity, 2),
                    'timing': weeks,
                    'next_date': next_date
                }
            except Exception as e:
                prediction = {'error': str(e)}

        elif 'submit_date_form' in request.form:
            user.last_fertilizer_date = request.form['last_fertilizer_date']
            db.session.commit()
            flash('Fertilizer date updated!', 'success')

    return render_template('fertilizer.html',
                         prediction=prediction,
                         last_date=user.last_fertilizer_date,
                         sensor_data=sensor_data)

@app.route('/harvest', methods=['GET', 'POST'])
@login_required
def harvest():
    sensor_data = get_sensor_data()
    prediction = None
    errors = []

    if request.method == 'POST':
        try:
            form_data = {
                'planting_date': request.form['planting_date'],
                'growth_stage': request.form['growth_stage'],
                'temperature': request.form['temperature'],
                'humidity': request.form['humidity'],
                'light_exposure': request.form['light_exposure'],
                'soil_moisture': request.form['soil_moisture'],
                'pesticide_used': request.form['pesticide_used']
            }

            # Validate inputs
            try:
                growth_stage = int(form_data['growth_stage'])
                if not (min_growth_stage <= growth_stage <= max_growth_stage):
                    errors.append(f"Growth Stage must be between {min_growth_stage} and {max_growth_stage} days")
            except:
                errors.append("Invalid Growth Stage value")

            try:
                temperature = float(form_data['temperature'])
                if not (10 <= temperature <= 40):
                    errors.append("Temperature must be between 10°C and 40°C")
            except:
                errors.append("Invalid Temperature value")

            if not errors:
                planting_date_ordinal = datetime.strptime(
                    form_data['planting_date'], "%Y-%m-%d").toordinal()

                new_data = pd.DataFrame([[
                    planting_date_ordinal,
                    int(form_data['growth_stage']),
                    float(form_data['temperature']),
                    float(form_data['humidity']),
                    float(form_data['light_exposure']),
                    float(form_data['soil_moisture']),
                    int(form_data['pesticide_used'])
                ]], columns=harvest_feature_names)

                predicted_days = predict_harvest(new_data)
                prediction = f"Predicted Harvest Days: {predicted_days:.0f} days"

        except Exception as e:
            errors.append(f"System error: {str(e)}")

    return render_template('harvest.html',
                         prediction=prediction,
                         errors=errors,
                         sensor_data=sensor_data)

@app.route('/history')
@login_required
def history():
    log_ref = firebase_db.reference('/prediction_logs')
    logs = log_ref.get()

    data = []
    if logs:
        log_items = logs.values() if isinstance(logs, dict) else logs
        for log in log_items:
            if not isinstance(log, dict):
                continue
            try:
                log["per_plant_water"] = float(log.get("per_plant_water", 0))
                log["total_water"] = float(log.get("total_water", 0))
            except (ValueError, TypeError):
                log["per_plant_water"] = 0
                log["total_water"] = 0
            data.append(log)

    data.sort(key=lambda x: str(x.get('timestamp', '')), reverse=True)
    return render_template('history.html', logs=data)

@app.route('/download_prediction_logs')
@login_required
def download_logs():
    ref = firebase_db.reference('/prediction_logs')
    logs = ref.get()

    if not logs:
        return "No logs found", 404

    import csv
    from io import StringIO
    si = StringIO()
    writer = csv.writer(si)

    writer.writerow(["timestamp", "temperature", "humidity", "soil_moisture", "light_level", "plant_count", "per_plant_water", "total_water"])

    log_items = logs.values() if isinstance(logs, dict) else logs
    for log in log_items:
        if isinstance(log, dict):
            writer.writerow([
                log.get("timestamp"),
                log.get("temperature"),
                log.get("humidity"),
                log.get("soil_moisture"),
                log.get("light_level"),
                log.get("plant_count"),
                log.get("per_plant_water"),
                log.get("total_water")
            ])

    output = si.getvalue()
    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=prediction_logs.csv"})

@app.route('/get_tank_height', methods=['GET'])
@login_required
def get_tank_height():
    ref = firebase_db.reference('/')
    tank_height = ref.child('tank_height_cm').get()
    if tank_height is None:
        tank_height = 100
    return jsonify({'tank_height_cm': tank_height})

@app.route('/set_tank_height', methods=['POST'])
@login_required
def set_tank_height():
    try:
        new_height = int(request.form['height'])
        if new_height <= 0:
            return jsonify({'message': 'Height must be positive'}), 400

        ref = firebase_db.reference('/')
        ref.update({'tank_height_cm': new_height})
        return jsonify({'message': 'Tank height updated successfully'})
    except Exception as e:
        return jsonify({'message': str(e)}), 500

@app.route('/update_irrigation_automation', methods=['POST'])
@login_required
def update_irrigation_automation():
    status = request.form.get('status')
    sensor_ref.update({'automation_irrigation': int(status)})
    return jsonify({"message": "Irrigation automation updated", "status": status})

@app.route('/update_alarm_automation', methods=['POST'])
@login_required
def update_alarm_automation():
    status = request.form.get('status')
    sensor_ref.update({'automated_alarm': int(status)})
    return jsonify({"message": "Alarm automation updated", "status": status})

@app.route('/update_fan_automation', methods=['POST'])
@login_required
def update_fan_automation():
    status = request.form.get('status')
    sensor_ref.update({'fan_automation': int(status)})
    return jsonify({"message": "Fan automation updated", "status": status})

@app.route('/update_buzzer_status', methods=['POST'])
@login_required
def update_buzzer_status():
    status = request.form.get('status')
    sensor_ref.update({'buzzer_status': int(status)})
    return jsonify({"message": "Buzzer status updated", "status": status})

@app.route('/update_vent_automation', methods=['POST'])
@login_required
def update_vent_automation():
    status = request.form.get('status')
    sensor_ref.update({'vent_status': int(status)})
    return jsonify({"message": "Ventilation automation updated", "status": status})

@app.route('/sensor_overview')
@login_required
def sensor_overview():
    return render_template('SensorData.html')

@app.route('/get_sensor_data_realtime', methods=['GET'])
@login_required
def get_sensor_data_realtime():
    sensor_data = sensor_ref.get()
    alerts = []

    if sensor_data:
        humidity = float(sensor_data.get('humidity', 0))
        temperature = float(sensor_data.get('temperature', 0))
        soil_moisture = float(sensor_data.get('soilMoisture', 0))
        water_level = float(sensor_data.get('waterLevel', 0))

        tank_height_ref = firebase_db.reference('/tank_height_cm').get()
        tank_height = float(tank_height_ref if tank_height_ref is not None else 100)

        distance = float(sensor_data.get('distance', 0))
        tank_percent = max(0.0, min(100.0, ((tank_height - distance) / tank_height) * 100))
        automation_irrigation = int(sensor_data.get('automation_irrigation', 0))
        fan_automation = int(sensor_data.get('fan_automation', 0))
        automated_alarm = int(sensor_data.get('automated_alarm', 0))

        # Alarm Buzzer Automation Conditions
        buzzer_triggered = False
        if automated_alarm == 1:
            if water_level > 1000:
                alerts.append("🚨 Water overflow detected! Buzzer activated.")
                buzzer_triggered = True
            if tank_percent < 10:
                alerts.append("⚠️ Water tank is below 10%. Buzzer activated.")
                buzzer_triggered = True
            if temperature > 40:
                alerts.append("🔥 High temperature! Buzzer activated.")
                buzzer_triggered = True
            if soil_moisture > 3500 and automation_irrigation == 0:
                alerts.append("🌱 Soil is dry. Enable auto irrigation or water manually. Buzzer activated.")
                buzzer_triggered = True
            if not buzzer_triggered:
                alerts.append("⚠️ Buzzer remains OFF. Because all conditions are optimal.")
        else:
            sensor_ref.update({'buzzer_status': 0})

        sensor_ref.update({'buzzer_status': 1 if buzzer_triggered else 0})

        # Irrigation Motor Automation Logic
        if automation_irrigation == 1:
            sensor_ref.update({'device1_status': 1})
            if tank_percent >= 20:
                if soil_moisture > 2500:
                    alerts.append("💧 Irrigation started: Soil moisture is low/dry, and water is available.")
                else:
                    alerts.append("ℹ️ Soil moisture is within optimal range. No need for irrigation at this time.")
            else:
                alerts.append("🚫 Warning: Water tank level is below 20%. Please refill the tank soon.")

            if water_level > 1000:
                alerts.append("⚠️ Warning: Water level in the plant pot is high. Monitor for potential overflow.")
        else:
            sensor_ref.update({'device1_status': 0})

        # Ventilation Fan Automation Conditions
        if fan_automation == 1:
            sensor_ref.update({'device2_status': 1})
            if temperature > 35 or humidity < 70:
                alerts.append("🌬️ Fan turned ON: Temperature is above 35°C or humidity low, cooling in progress.")
            elif temperature <= 35:
                alerts.append("✅ Fan turned OFF: Temperature is at or below 35°C, cooling not needed.")
        else:
            sensor_ref.update({'device2_status': 0})

        data = {
            'distance': sensor_data.get('distance', 'N/A'),
            'waterLevel': sensor_data.get('waterLevel', 'N/A'),
            'humidity': sensor_data.get('humidity', 'N/A'),
            'lightLevel': sensor_data.get('lightLevel', 'N/A'),
            'soilMoisture': sensor_data.get('soilMoisture', 'N/A'),
            'temperature': sensor_data.get('temperature', 'N/A'),
            'device1_status': sensor_data.get('device1_status', 'N/A'),
            'device2_status': sensor_data.get('device2_status', 'N/A'),
            'fan_automation': fan_automation,
            'automation_irrigation': automation_irrigation,
            'automated_alarm': automated_alarm,
            'vent_status': int(sensor_data.get('vent_status', 0)),
            'alerts': alerts,
            'buzzer_status': int(sensor_data.get('buzzer_status', 0)),
            'is_simulation': USE_SIMULATION
        }
    else:
        data = {'message': 'No sensor data available'}
    return jsonify(data)

# ---------------------------------------------------------
# Simulation Control Endpoints
# ---------------------------------------------------------
@app.route('/api/simulation/preset', methods=['POST'])
@login_required
def simulation_preset():
    preset = request.form.get('preset', 'normal')
    tank_height = simulated_store.get_tank_height()

    if preset == 'normal':
        simulated_store.update_sensors({
            'temperature': 27.5,
            'humidity': 68.0,
            'soilMoisture': 2200.0,
            'distance': 25.0,
            'waterLevel': 120.0,
            'lightLevel': 12.0
        })
    elif preset == 'heatwave':
        simulated_store.update_sensors({
            'temperature': 41.5,
            'humidity': 35.0,
            'soilMoisture': 3600.0,
            'distance': 30.0,
            'waterLevel': 100.0,
            'lightLevel': 18.0
        })
    elif preset == 'dry_soil':
        simulated_store.update_sensors({
            'temperature': 31.0,
            'humidity': 50.0,
            'soilMoisture': 3700.0,
            'distance': 30.0,
            'waterLevel': 80.0,
            'lightLevel': 14.0,
            'automation_irrigation': 0 # Turn off auto irrigation to trigger alarm
        })
    elif preset == 'low_tank':
        simulated_store.update_sensors({
            'temperature': 28.0,
            'humidity': 60.0,
            'soilMoisture': 3000.0,
            'distance': float(tank_height) * 0.95, # 95% distance = 5% water left (<10%)
            'waterLevel': 100.0,
            'lightLevel': 10.0
        })
    elif preset == 'overflow':
        simulated_store.update_sensors({
            'temperature': 28.0,
            'humidity': 65.0,
            'soilMoisture': 1500.0,
            'distance': 20.0,
            'waterLevel': 1150.0,
            'lightLevel': 10.0
        })
    elif preset == 'refill':
        simulated_store.update_sensors({
            'distance': 10.0 # Tank 90% full
        })

    return jsonify({"message": f"Preset '{preset}' applied successfully", "sensors": simulated_store.get_sensors()})

@app.route('/api/simulation/update', methods=['POST'])
@login_required
def simulation_update():
    try:
        updates = {}
        for key in ['temperature', 'humidity', 'soilMoisture', 'waterLevel', 'distance', 'lightLevel']:
            if key in request.form and request.form[key] != '':
                updates[key] = float(request.form[key])
        simulated_store.update_sensors(updates)
        return jsonify({"message": "Simulated values updated", "sensors": simulated_store.get_sensors()})
    except Exception as e:
        return jsonify({"message": str(e)}), 400

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)