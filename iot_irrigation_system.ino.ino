#include "DHT.h"
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <Firebase_ESP_Client.h>
#include <ESP32Servo.h>

// -------------------- Configuration --------------------
#define WIFI_SSID        "#enter wifi ssid name here"
#define WIFI_PASSWORD    "#enter wifi password here"

#define API_KEY          "#enter API_KEY here"
#define DATABASE_URL     "#enter database_url here"

#define SCREEN_WIDTH     128
#define SCREEN_HEIGHT    64
#define OLED_RESET       -1
#define OLED_I2C_ADDRESS 0x3C

// -------------------- Pin Definitions --------------------
#define DHTPIN            15
#define DHTTYPE           DHT11
#define TRIG_PIN          27
#define ECHO_PIN          18
#define SIGNAL_PIN        34
#define LIGHT_SENSOR_PIN  32
#define WATER_LEVEL_PIN   33
#define RELAY_DEVICE1     5
#define RELAY_DEVICE2     16
#define BUZZER_PIN        25
#define SERVO_PIN         4

// -------------------- Objects --------------------
DHT dht(DHTPIN, DHTTYPE);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;
Servo ventServo;

// -------------------- Variables --------------------
bool signupOK = false;
bool buzzerState = false;
bool ventInitialized = false;

float temperature = 0, humidity = 0, distance = 0;
int soilMoisture = 0, lightLevel = 0, waterLevel = 0;
int device1State = 0, device2State = 0;
int buzzerFirebaseValue = 0, lastBuzzerValue = 0, ventStatus = 0;

unsigned long lastUpload = 0;
unsigned long buzzerLastToggle = 0;
const unsigned long uploadInterval = 1000;

// -------------------- Wi-Fi Connection --------------------
void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(300);
  }
  Serial.println("\n✅ Connected to Wi-Fi");
}

// -------------------- Firebase Initialization --------------------
void initFirebase() {
  config.api_key = API_KEY;
  config.database_url = DATABASE_URL;
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);

  if (Firebase.signUp(&config, &auth, "", "")) {
    Serial.println("✅ Firebase Auth OK");
    signupOK = true;
  } else {
    Serial.printf("❌ Firebase Auth Failed: %s\n", config.signer.signupError.message.c_str());
  }
}

// -------------------- Hardware Initialization --------------------
void initHardware() {
  dht.begin();
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(SIGNAL_PIN, INPUT);
  pinMode(LIGHT_SENSOR_PIN, INPUT);
  pinMode(WATER_LEVEL_PIN, INPUT);
  pinMode(RELAY_DEVICE1, OUTPUT);
  pinMode(RELAY_DEVICE2, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  digitalWrite(RELAY_DEVICE1, HIGH);
  digitalWrite(RELAY_DEVICE2, HIGH);
  digitalWrite(BUZZER_PIN, LOW);

  ventServo.attach(SERVO_PIN);
}

// -------------------- OLED Display Initialization --------------------
void initDisplay() {
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS)) {
    Serial.println(F("❌ OLED init failed"));
    while (true);
  }
  display.clearDisplay();
  display.display();
}

// -------------------- Sensor Reading --------------------
float getUltrasonicDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  return (duration > 0) ? (duration * 0.0343) / 2.0 : 0.0;
}

void readSensors() {
  temperature = dht.readTemperature();
  humidity = dht.readHumidity();
  distance = getUltrasonicDistance();
  soilMoisture = analogRead(SIGNAL_PIN);
  lightLevel = analogRead(LIGHT_SENSOR_PIN);
  waterLevel = analogRead(WATER_LEVEL_PIN);
}

// -------------------- OLED Display Update --------------------
void printSensor(String label, String value, int &y) {
  display.setCursor(0, y);
  display.print(label);
  display.println(value);
  display.drawLine(0, y + 8, 128, y + 8, SSD1306_WHITE);
  y += 10;
}

void updateDisplay() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  int y = 0;

  printSensor("Temperature : ", String(temperature) + " C", y);
  printSensor("Humidity    : ", String(humidity) + " %", y);
  printSensor("Distance    : ", String(distance) + " cm", y);
  printSensor("Soil        : ", String(soilMoisture), y);
  printSensor("Light       : ", String(lightLevel), y);
  printSensor("Water       : ", String(waterLevel), y);
  printSensor("D1: " + String(device1State ? "ON(1)" : "OFF(0)") + 
              " | D2: " + String(device2State ? "ON(1)" : "OFF(0)"), "", y);

  display.display();
}

// -------------------- Firebase Upload --------------------
void uploadSensorData() {
  if (Firebase.ready() && signupOK) {
    Firebase.RTDB.setFloat(&fbdo, "/sensors/temperature", temperature);
    Firebase.RTDB.setFloat(&fbdo, "/sensors/humidity", humidity);
    Firebase.RTDB.setFloat(&fbdo, "/sensors/distance", distance);
    Firebase.RTDB.setInt(&fbdo, "/sensors/soilMoisture", soilMoisture);
    Firebase.RTDB.setInt(&fbdo, "/sensors/lightLevel", lightLevel);
    Firebase.RTDB.setInt(&fbdo, "/sensors/waterLevel", waterLevel);
    Serial.println("✅ Firebase updated");
  }
}

// -------------------- Firebase Sync --------------------
void syncRelayStates() {
  if (!Firebase.ready() || !signupOK) return;

  if (Firebase.RTDB.getInt(&fbdo, "/sensors/device1_status") && fbdo.dataType() == "int") {
    device1State = fbdo.intData();
    bool shouldTurnOn = (device1State == 1 && distance < 10 && waterLevel < 1000 && soilMoisture > 2500);
    digitalWrite(RELAY_DEVICE1, shouldTurnOn ? LOW : HIGH);
  }

  if (Firebase.RTDB.getInt(&fbdo, "/sensors/device2_status") && fbdo.dataType() == "int") {
    device2State = fbdo.intData();
    digitalWrite(RELAY_DEVICE2, (device2State == 1 && (temperature > 35 || humidity < 70)) ? LOW : HIGH);
  }
}

void syncBuzzerState() {
  if (!Firebase.ready() || !signupOK) return;

  if (Firebase.RTDB.getInt(&fbdo, "/sensors/buzzer_status") && fbdo.dataType() == "int") {
    buzzerFirebaseValue = fbdo.intData();

    if (buzzerFirebaseValue == 1) {
      if (millis() - buzzerLastToggle >= 100) {
        buzzerLastToggle = millis();
        buzzerState = !buzzerState;
        digitalWrite(BUZZER_PIN, buzzerState ? HIGH : LOW);
      }
    } else {
      if (lastBuzzerValue != buzzerFirebaseValue) {
        digitalWrite(BUZZER_PIN, LOW);
        buzzerState = false;
      }
    }

    lastBuzzerValue = buzzerFirebaseValue;
  }
}

void syncVentilation() {
  if (!Firebase.ready() || !signupOK) return;

  if (Firebase.RTDB.getInt(&fbdo, "/sensors/vent_status") && fbdo.dataType() == "int") {
    ventStatus = fbdo.intData();
    ventServo.write((ventStatus == 1 && temperature > 35) ? 0 : 120);
    ventInitialized = true;
  }
}

// -------------------- Main --------------------
void setup() {
  Serial.begin(115200);
  connectWiFi();
  initFirebase();
  initHardware();
  initDisplay();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();

  readSensors();
  updateDisplay();
  syncRelayStates();
  syncBuzzerState();
  syncVentilation();

  if (millis() - lastUpload >= uploadInterval) {
    lastUpload = millis();
    uploadSensorData();
  }
}
