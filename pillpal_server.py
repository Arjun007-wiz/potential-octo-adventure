import serial
import threading
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime
from flask import Flask, request, redirect, jsonify
from plyer import notification

# ---------- SETTINGS ----------
PORT = "COM5"          # change to your Arduino port
BAUD = 9600
MAX_BINS = 7           # must match the Arduino's BINS number

# Paste your Anthropic API key here to turn on AI interaction checks.
# Leave as "" to use only the openFDA lookup (no AI).
ANTHROPIC_API_KEY = ""
AI_MODEL = "claude-sonnet-4-5-20250929"

app = Flask(__name__)

schedule = [{"drug": "", "count": 0, "times": []} for _ in range(4)]

ard = None
log = []
fired_today = set()
drug_report = ""
checking = False


def notify(title, message):
    try:
        notification.notify(title=title, message=message, timeout=10)
    except Exception:
        pass
    print(f"[NOTIFY] {title} - {message}")
    log.insert(0, f"{datetime.now():%H:%M:%S}  {title}: {message}")
    del log[20:]


def connect_arduino():
    global ard
    try:
        ard = serial.Serial(PORT, BAUD, timeout=1)
        time.sleep(2)
        print(f"Connected to Arduino on {PORT}")
        notify("PillPal", "Device connected.")
    except Exception as e:
        print("Could not open serial port:", e)
        ard = None


def serial_loop():
    while True:
        if ard and ard.in_waiting:
            line = ard.readline().decode(errors="ignore").strip()
            if line:
                print("Arduino:", line)
                handle_message(line)

        now = datetime.now().strftime("%H:%M")
        for i, b in enumerate(schedule):
            key = f"{i}-{now}"
            if now in b["times"] and key not in fired_today:
                fired_today.add(key)
                send_dose(i)

        if now == "00:00":
            fired_today.clear()

        time.sleep(0.3)


def send_dose(bin_index):
    drug = schedule[bin_index]["drug"] or f"Bin {bin_index + 1}"
    notify("PillPal - Time for your medication", f"Take: {drug}")
    if ard:
        ard.write(b"DOSE\n")


def handle_message(line):
    if line.startswith(">> DOSE TAKEN"):
        notify("PillPal", "Dose taken. Well done!")
    elif line.startswith(">> DOSE MISSED"):
        notify("PillPal - MISSED DOSE", "A dose was not taken!")
    elif line.startswith("REFILL:NEEDED"):
        notify("PillPal - Refill needed", "Pills finished. Please refill.")
    elif line.startswith("ERROR"):
        notify("PillPal - Error", "A pill did not dispense correctly.")


# ================= DRUG LOOKUP (openFDA) =================

def lookup_drug(name):
    try:
        q = urllib.parse.quote(name)
        url = ("https://api.fda.gov/drug/label.json?"
               "search=openfda.brand_name:" + q + "+openfda.generic_name:" + q + "&limit=1")
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())

        if not data.get("results"):
            return {"name": name, "found": False}

        res = data["results"][0]

        def first(field):
            v = res.get(field)
            if isinstance(v, list) and v:
                return v[0][:300]
            return ""

        return {
            "name": name,
            "found": True,
            "purpose": first("purpose") or first("indications_and_usage"),
            "warnings": first("warnings"),
            "interactions": first("drug_interactions"),
        }
    except Exception as e:
        return {"name": name, "found": False, "error": str(e)}


# ================= AI INTERACTION CHECK =================

def ai_check(drug_list, fda_info):
    if not ANTHROPIC_API_KEY:
        return None

    info_text = ""
    for d in fda_info:
        if d.get("found"):
            info_text += ("\n" + d["name"] + ":\n"
                          "  Purpose: " + d.get("purpose", "") + "\n"
                          "  Interactions: " + d.get("interactions", "")[:400] + "\n")

    prompt = (
        "You are helping a medication reminder device. Here is a patient's list "
        "of medications and some official label data.\n\n"
        "Medications: " + ", ".join(drug_list) + "\n"
        "Label data:" + info_text + "\n\n"
        "In simple, plain language a non-medical person can understand:\n"
        "1. Flag any dangerous combinations between these drugs.\n"
        "2. Note any important timing/food instructions.\n"
        "Keep it short and clear. If nothing concerning, say so. "
        "End by reminding them to confirm with a pharmacist."
    )

    try:
        body = json.dumps({
            "model": AI_MODEL,
            "max_tokens": 700,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        return "".join(part.get("text", "") for part in data.get("content", []))
    except Exception as e:
        return "(AI check failed: " + str(e) + ")"


def run_drug_check():
    global drug_report, checking
    checking = True
    drug_report = "Checking medications..."

    names = [b["drug"].strip() for b in schedule if b["drug"].strip()]
    if not names:
        drug_report = "No medications entered yet."
        checking = False
        return

    fda_info = [lookup_drug(n) for n in names]

    lines = ["MEDICATION LOOKUP (openFDA):"]
    for d in fda_info:
        if d.get("found"):
            lines.append("\n- " + d["name"])
            if d.get("purpose"):
                lines.append("   Purpose: " + d["purpose"])
            if d.get("interactions"):
                lines.append("   Interactions: " + d["interactions"][:250] + "...")
        else:
            lines.append("\n- " + d["name"] + ": not found in database")

    ai = ai_check(names, fda_info)
    if ai:
        lines.append("\n\nAI INTERACTION REVIEW:\n" + ai)
    else:
        lines.append("\n\n(Add an Anthropic API key in the code to enable the AI interaction review.)")

    drug_report = "\n".join(lines)
    checking = False
    notify("PillPal", "Medication check complete.")


# ---------- PAGE ----------

def render_page():
    bins_html = ""
    for i in range(len(schedule)):
        b = schedule[i]
        times_str = ", ".join(b["times"])
        bins_html += (
            '\n        <div class="bin">\n'
            '          <h3>Bin ' + str(i + 1) + '</h3>\n'
            '          <label>Medication name</label>\n'
            '          <input name="drug' + str(i) + '" value="' + b["drug"] + '" placeholder="e.g. Metformin">\n'
            '          <label>Number of pills</label>\n'
            '          <input name="count' + str(i) + '" type="number" value="' + str(b["count"]) + '" placeholder="30">\n'
            '          <label>Dose times (comma separated, 24-hour)</label>\n'
            '          <input name="times' + str(i) + '" value="' + times_str + '" placeholder="08:00, 18:00">\n'
            '        </div>')

    return (
'<!DOCTYPE html>\n'
'<html>\n'
'<head>\n'
'  <title>PillPal Setup</title>\n'
'  <meta charset="utf-8">\n'
'  <style>\n'
'    body { font-family: system-ui, sans-serif; background:#f4f6f8; color:#222;\n'
'           max-width:640px; margin:40px auto; padding:0 20px; }\n'
'    h1 { color:#1D9E75; }\n'
'    .bin { background:#fff; border:1px solid #ddd; border-radius:10px;\n'
'           padding:16px 20px; margin-bottom:16px; }\n'
'    .bin h3 { margin:0 0 12px; color:#378ADD; }\n'
'    label { display:block; font-size:13px; color:#555; margin:8px 0 3px; }\n'
'    input { width:100%; padding:8px; border:1px solid #ccc; border-radius:6px;\n'
'            box-sizing:border-box; font-size:14px; }\n'
'    button { color:#fff; border:none; padding:12px 24px; border-radius:8px;\n'
'             font-size:15px; cursor:pointer; background:#1D9E75; margin-top:8px; }\n'
'    .hint { font-size:12px; color:#888; }\n'
'    #log, #report { background:#fff; border:1px solid #ddd; border-radius:10px;\n'
'           padding:12px 16px; margin-top:24px; font-size:13px; }\n'
'    #log div { padding:3px 0; border-bottom:1px solid #f0f0f0; }\n'
'    #reportbody { white-space:pre-wrap; font-size:13px; line-height:1.5; }\n'
'  </style>\n'
'</head>\n'
'<body>\n'
'  <h1>PillPal Setup</h1>\n'
'  <p class="hint">Enter each bin\'s medication, how many pills, and the dose times.</p>\n'
'\n'
'  <form action="/save" method="post">\n'
'    ' + bins_html + '\n'
'    <button type="submit">Save Schedule</button>\n'
'  </form>\n'
'\n'
'  <div style="margin-top:16px;">\n'
'    <form action="/add" method="post" style="display:inline;">\n'
'      <button type="submit" style="background:#378ADD;">+ Add Bin</button>\n'
'    </form>\n'
'    <form action="/remove" method="post" style="display:inline;">\n'
'      <button type="submit" style="background:#888;">- Remove Bin</button>\n'
'    </form>\n'
'    <form action="/check" method="post" style="display:inline;">\n'
'      <button type="submit" style="background:#D8402F;">Check Interactions</button>\n'
'    </form>\n'
'    <p class="hint">Up to ' + str(MAX_BINS) + ' bins. "Check Interactions" looks up each drug and reviews them together.</p>\n'
'  </div>\n'
'\n'
'  <div id="report">\n'
'    <strong>Medication check</strong>\n'
'    <div id="reportbody">Press "Check Interactions" after saving your medications.</div>\n'
'  </div>\n'
'\n'
'  <div id="log">\n'
'    <strong>Recent activity</strong>\n'
'    <div id="logbody">Loading...</div>\n'
'  </div>\n'
'\n'
'  <script>\n'
'    async function refreshLog() {\n'
'      const r = await fetch("/log");\n'
'      const items = await r.json();\n'
'      document.getElementById("logbody").innerHTML =\n'
'        items.length ? items.map(x => `<div>${x}</div>`).join("") : "<div>No activity yet.</div>";\n'
'    }\n'
'    async function refreshReport() {\n'
'      const r = await fetch("/report");\n'
'      const d = await r.json();\n'
'      if (d.text) document.getElementById("reportbody").textContent = d.text;\n'
'    }\n'
'    refreshLog(); refreshReport();\n'
'    setInterval(refreshLog, 3000);\n'
'    setInterval(refreshReport, 2000);\n'
'  </script>\n'
'</body>\n'
'</html>')


# ---------- ROUTES ----------

@app.route("/")
def home():
    return render_page()


@app.route("/save", methods=["POST"])
def save():
    for i in range(len(schedule)):
        schedule[i]["drug"] = request.form.get("drug" + str(i), "").strip()
        try:
            schedule[i]["count"] = int(request.form.get("count" + str(i), 0))
        except ValueError:
            schedule[i]["count"] = 0
        raw = request.form.get("times" + str(i), "")
        schedule[i]["times"] = [t.strip() for t in raw.split(",") if t.strip()]
    notify("PillPal", "Schedule saved.")
    return redirect("/")


@app.route("/add", methods=["POST"])
def add_bin():
    if len(schedule) < MAX_BINS:
        schedule.append({"drug": "", "count": 0, "times": []})
    else:
        notify("PillPal", "Max " + str(MAX_BINS) + " bins (device limit).")
    return redirect("/")


@app.route("/remove", methods=["POST"])
def remove_bin():
    if len(schedule) > 1:
        schedule.pop()
    return redirect("/")


@app.route("/check", methods=["POST"])
def check():
    threading.Thread(target=run_drug_check, daemon=True).start()
    return redirect("/")


@app.route("/report")
def get_report():
    return jsonify({"text": drug_report, "checking": checking})


@app.route("/log")
def get_log():
    return jsonify(log)


if __name__ == "__main__":
    connect_arduino()
    threading.Thread(target=serial_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
