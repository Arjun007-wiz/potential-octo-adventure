// ---------- PINS ----------
const int BINS = 7;   // set to your real number of compartments
const int irChute  = 2;   // IR 1 - did the pill drop?
const int irTray   = 3;   // IR 2 - is the pill still sitting there?
const int greenLed = 5;
const int nextBtn  = 6;
const int redLed   = 8;

// ---------- SENSOR POLARITY ----------
const int BLOCKED = LOW;

// ---------- BINS ----------

const int PILLS_PER_BIN = 30;

int pillsLoaded[BINS];
int pillsTaken[BINS];
int totalLoaded = 0;
int totalTaken  = 0;

// ---------- STATE ----------
enum Mode { LOADING, RUNNING };
Mode mode = LOADING;
int loadBin = 0;

// ---------- BUTTON ----------
const unsigned long DEBOUNCE = 50;
bool btnLast = HIGH;
unsigned long btnChanged = 0;
unsigned long holdStart = 0;

// ---------- DOSE TIMING ----------
const unsigned long dropTimeout  = 5000;
const unsigned long takeTimeout  = 15000;
int currentBin = 0;

// ---------- IR MONITORING ----------
int lastChute = -1;
int lastTray  = -1;

void setup() {
  Serial.begin(9600);
  pinMode(irChute, INPUT);
  pinMode(irTray, INPUT);
  pinMode(redLed, OUTPUT);
  pinMode(greenLed, OUTPUT);
  pinMode(nextBtn, INPUT_PULLUP);

  startLoading();
}

void loop() {
  reportSensors();

  if (mode == LOADING) handleLoading();
  else                 handleRunning();
}

// ---------- prints IR state whenever it changes ----------
void reportSensors() {
  int c = digitalRead(irChute);
  int t = digitalRead(irTray);

  if (c != lastChute || t != lastTray) {
    Serial.print("[IR] Chute: ");
    Serial.print(c == BLOCKED ? "BLOCKED" : "clear  ");
    Serial.print("   Tray: ");
    Serial.println(t == BLOCKED ? "BLOCKED" : "clear");
    lastChute = c;
    lastTray = t;
  }
}

// ================= LOADING =================

void startLoading() {
  mode = LOADING;
  loadBin = 0;
  totalLoaded = 0;
  totalTaken = 0;
  for (int i = 0; i < BINS; i++) { pillsLoaded[i] = 0; pillsTaken[i] = 0; }

  digitalWrite(greenLed, LOW);
  digitalWrite(redLed, HIGH);
  Serial.println();
  Serial.println("===== LOADING =====");
  Serial.println("Fill the bin, then press the button.");
  showBinPrompt();
}

void showBinPrompt() {
  Serial.print("Fill Bin ");
  Serial.print(loadBin + 1);
  Serial.print(" (");
  Serial.print(PILLS_PER_BIN);
  Serial.println(" pills), then press button.");
}

void handleLoading() {
  if (buttonPressed()) confirmBin();
}

void confirmBin() {
  pillsLoaded[loadBin] = PILLS_PER_BIN;
  totalLoaded += PILLS_PER_BIN;

  Serial.print(">> Bin ");
  Serial.print(loadBin + 1);
  Serial.print(" confirmed: ");
  Serial.print(PILLS_PER_BIN);
  Serial.println(" pills");
  blinkGreen(1, 100);

  loadBin++;
  if (loadBin >= BINS) finishLoading();
  else                 showBinPrompt();
}

void finishLoading() {
  digitalWrite(redLed, LOW);
  mode = RUNNING;
  currentBin = 0;

  Serial.println("===== LOADING COMPLETE =====");
  printInventory();
  Serial.println("Waiting for dose command from laptop...");
  blinkGreen(2, 120);
}

// ================= RUNNING =================

void handleRunning() {
  // hold button 1.5s to reload
  if (digitalRead(nextBtn) == LOW) {
    if (holdStart == 0) holdStart = millis();
    if (millis() - holdStart > 1500) { holdStart = 0; startLoading(); return; }
  } else {
    holdStart = 0;
  }

  // wait for Python to send "DOSE"
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "DOSE") runDose();
  }
}

void runDose() {
  int tries = 0;
  while (pillsLoaded[currentBin] - pillsTaken[currentBin] <= 0 && tries < BINS) {
    currentBin = (currentBin + 1) % BINS;
    tries++;
  }
  if (tries >= BINS) { allPillsFinished(); return; }

  Serial.println("========================");
  Serial.print("DOSE TIME - bin ");
  Serial.println(currentBin + 1);

  alertUser();

  if (waitForDrop()) {
    Serial.println("Pill dispensed.");
    if (waitForTaken()) {
      pillsTaken[currentBin]++;
      totalTaken++;
      Serial.println(">> DOSE TAKEN");
      confirmGreen();
      printInventory();
    } else {
      Serial.println(">> DOSE MISSED");
      missedAlert();
    }
  } else {
    Serial.println("ERROR: no pill detected in chute.");
    errorFlash();
  }

  if (totalTaken >= totalLoaded) allPillsFinished();
  currentBin = (currentBin + 1) % BINS;
}

bool waitForDrop() {
  unsigned long t = millis();
  while (millis() - t < dropTimeout) {
    reportSensors();
    if (digitalRead(irChute) == BLOCKED) return true;
  }
  return false;
}

bool waitForTaken() {
  Serial.println("Waiting for pill to be taken...");
  unsigned long t = millis();
  while (millis() - t < takeTimeout) {
    reportSensors();
    if (digitalRead(irTray) != BLOCKED) return true;
    delay(50);
  }
  return false;
}

// ================= INVENTORY =================

void printInventory() {
  Serial.println("--- INVENTORY ---");
  for (int i = 0; i < BINS; i++) {
    Serial.print("  Bin ");
    Serial.print(i + 1);
    Serial.print(": ");
    Serial.print(pillsLoaded[i] - pillsTaken[i]);
    Serial.print(" left of ");
    Serial.println(pillsLoaded[i]);
  }
  Serial.print("  TOTAL: ");
  Serial.print(totalTaken);
  Serial.print(" taken of ");
  Serial.println(totalLoaded);
  Serial.println("-----------------");
}

void allPillsFinished() {
  Serial.println();
  Serial.println("*** ALL PILLS FINISHED - REFILL NEEDED ***");
  Serial.println("REFILL:NEEDED");
  printInventory();
  for (int i = 0; i < 4; i++) {
    digitalWrite(redLed, HIGH); delay(200);
    digitalWrite(redLed, LOW);  delay(200);
  }
  startLoading();
}

// ================= BUTTON =================

bool buttonPressed() {
  bool reading = digitalRead(nextBtn);
  if (reading != btnLast && millis() - btnChanged > DEBOUNCE) {
    btnChanged = millis();
    btnLast = reading;
    if (reading == LOW) return true;
  }
  return false;
}

// ================= LED SIGNALS =================

void alertUser() {
  digitalWrite(greenLed, LOW);
  digitalWrite(redLed, HIGH);
}

void confirmGreen() {
  digitalWrite(redLed, LOW);
  digitalWrite(greenLed, HIGH);
  delay(3000);
  digitalWrite(greenLed, LOW);
}

void blinkGreen(int times, int ms) {
  for (int i = 0; i < times; i++) {
    digitalWrite(greenLed, HIGH); delay(ms);
    digitalWrite(greenLed, LOW);  delay(ms);
  }
}

void errorFlash() {
  for (int i = 0; i < 10; i++) {
    digitalWrite(redLed, HIGH); delay(100);
    digitalWrite(redLed, LOW);  delay(100);
  }
}

void missedAlert() {
  for (int i = 0; i < 6; i++) {
    digitalWrite(redLed, HIGH); delay(350);
    digitalWrite(redLed, LOW);  delay(300);
  }
}
