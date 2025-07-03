# app.py
from flask import Flask, request, jsonify, render_template
import re
from threading import Lock

# Initialize the Flask application
app = Flask(__name__)

# --- In-Memory Data Store ---
# In a real application, you would use a database (like SQLite, PostgreSQL).
# For this project, we'll store the data in a Python dictionary.
# NOTE: This data will reset if the server restarts.
meter_data = {
    "totalEnergy_prepaid": 0.0,
    "totalEnergy_postpaid": 0.0,
    "credit_kwh": 0.0,
    "unpaid_balance_ksh": 0.0,
    "mode": "prepaid",  # 'prepaid' or 'postpaid'
    "last_command_for_esp": None # A message queue for the ESP32
}

# The billing rate must match the one on your ESP32
BILLING_RATE = 10.0  # KSh per kWh

# A lock to prevent race conditions when multiple requests access meter_data at once
data_lock = Lock()


# # --- Route for the ESP32 to poll for status/commands ---
# @app.route('/', methods=['GET'])
# def handle_esp_poll():
#     """
#     This endpoint is called by the ESP32 periodically.
#     It checks if there's a new command/message waiting for it.
#     Example URL called by ESP32: https://your-app.onrender.com/?command=status
#     """
#     # The ESP32 code uses ?command=status
#     if request.args.get('command') == 'status':
#         with data_lock:
#             # Check if there is a pending command
#             if meter_data['last_command_for_esp']:
#                 command = meter_data['last_command_for_esp']
#                 meter_data['last_command_for_esp'] = None  # Clear the command after sending
#                 print(f"Sent command to ESP32: {command}")
#                 return jsonify({"message": command})
#             else:
#                 return jsonify({"message": "OK. No new commands."})
                
#     # A simple message for anyone browsing the root URL
#     return "Smart Energy Meter Flask Server is running."


# # --- Route for the ESP32 to send its energy data ---
# @app.route('/', methods=['POST'])
# def receive_energy_data():
#     """
#     This endpoint receives JSON data from the ESP32.
#     ESP32 sends: {"totalEnergy": 123.45}
#     """
#     data = request.get_json()
#     if not data or 'totalEnergy' not in data:
#         return jsonify({"status": "error", "message": "Invalid data format"}), 400

#     received_energy = data['totalEnergy']

#     with data_lock:
#         if meter_data['mode'] == 'prepaid':
#             meter_data['totalEnergy_prepaid'] = received_energy
#         else: # postpaid
#             meter_data['totalEnergy_postpaid'] = received_energy
#             # Update the balance in real-time
#             meter_data['unpaid_balance_ksh'] = meter_data['totalEnergy_postpaid'] * BILLING_RATE
        
#         print(f"Received data from ESP32. Current state: {meter_data}")

#     return jsonify({"status": "success", "message": "Data received"})

# --- NEW ROUTE for the ESP32 to poll for status/commands ---
@app.route('/get_status', methods=['GET']) # Changed from '/' to '/get_status'
def handle_esp_poll():
    """
    This endpoint is called by the ESP32 periodically.
    It checks if there's a new command/message waiting for it.
    """
    with data_lock:
        if meter_data['last_command_for_esp']:
            command = meter_data['last_command_for_esp']
            meter_data['last_command_for_esp'] = None  # Clear command after sending
            print(f"Sent command to ESP32: {command}")
            return jsonify({"message": command})
        else:
            return jsonify({"message": "OK. No new commands."})


# --- NEW ROUTE for the ESP32 to send its energy data ---
@app.route('/log_energy', methods=['POST']) # Changed from '/' to '/log_energy'
def receive_energy_data():
    """
    This endpoint receives JSON data from the ESP32.
    """
    data = request.get_json()
    if not data or 'totalEnergy' not in data:
        return jsonify({"status": "error", "message": "Invalid data format"}), 400

    received_energy = data['totalEnergy']

    with data_lock:
        if meter_data['mode'] == 'prepaid':
            meter_data['totalEnergy_prepaid'] = received_energy
        else: # postpaid
            meter_data['totalEnergy_postpaid'] = received_energy
            meter_data['unpaid_balance_ksh'] = meter_data['totalEnergy_postpaid'] * BILLING_RATE
        
        # We don't need a detailed log here anymore, the endpoint says what it does.
        # print(f"Received data from ESP32. Current state: {meter_data}")

    return jsonify({"status": "success", "message": "Data received"})

# --- Route for the SMS Webhook (from Twilio) ---
@app.route('/sms', methods=['POST', 'GET']) # Allow GET for easier browser testing
def handle_sms_webhook():
    """
    This endpoint is configured as a webhook in your SMS provider (e.g., Twilio).
    When an SMS is received by your Twilio number, Twilio sends the message here.
    """
    # Twilio sends the message body in the 'Body' field of the form data.
    # We use request.values.get() to work with both GET and POST requests for easy testing.
    sms_text = request.values.get('Body', 'No message body found').strip()
    print(f"Received SMS: {sms_text}")
    
    # Use regular expressions to parse the M-Pesa message format
    # Example: "EA54... Confirmed. ... Ksh50.00 received from ..."
    match = re.search(r"Ksh([\d,]+\.\d{2})", sms_text)

    if not match:
        print("Could not find payment amount in SMS.")
        return "Could not parse payment from message.", 400

    # Extract amount, remove commas, and convert to a float
    amount_str = match.group(1).replace(',', '')
    paid_amount_ksh = float(amount_str)

    with data_lock:
        # Update the meter data based on the current mode
        if meter_data['mode'] == 'prepaid':
            credit_to_add_kwh = paid_amount_ksh / BILLING_RATE
            meter_data['credit_kwh'] += credit_to_add_kwh
            
            # Prepare a command for the ESP32 to fetch
            meter_data['last_command_for_esp'] = f"Credit: {meter_data['credit_kwh']:.4f} kWh"
            print(f"Processed prepaid payment. New credit: {meter_data['credit_kwh']:.4f} kWh")
        else: # postpaid
            meter_data['unpaid_balance_ksh'] -= paid_amount_ksh
            if meter_data['unpaid_balance_ksh'] < 0:
                meter_data['unpaid_balance_ksh'] = 0.0 # Balance can't be negative
            
            # Prepare a command for the ESP32
            meter_data['last_command_for_esp'] = f"Balance: {meter_data['unpaid_balance_ksh']:.2f} KSh"
            print(f"Processed postpaid payment. New balance: {meter_data['unpaid_balance_ksh']:.2f} KSh")

    # Respond to the webhook request
    return "Payment processed successfully.", 200


# --- A simple dashboard to view the current meter state ---
@app.route('/dashboard')
def dashboard():
    """
    A simple webpage to display the current state of the meter data for debugging.
    """
    with data_lock:
        return jsonify(meter_data)

# +++ NEW ROUTE TO SERVE THE HTML TESTING PAGE +++
@app.route('/tester')
def mpesa_tester():
    """Serves the HTML page for simulating M-Pesa payments."""
    return render_template('index.html')

if __name__ == '__main__':
    # For local testing, runs on http://127.0.0.1:5000
    app.run(debug=True)