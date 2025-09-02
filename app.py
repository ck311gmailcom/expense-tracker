from flask import Flask, render_template, request, redirect
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os, json, gspread, pytz

app = Flask(__name__)

# Set up Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
# Use the Render secret file path
secret_path = "/etc/secrets/GOOGLE_CREDS"
with open(secret_path) as f:
    creds_dict = json.load(f)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open("Official_Budget").worksheet("Expense Responses")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Generate timestamp
        tz = pytz.timezone("America/New_York")
        timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        purchaseDate = request.form.get("purchase_date")
        itemDesc = request.form.get("item_description")

        # Safe float conversion
        try:
            totalAmount = float(request.form.get("total_amount") or 0)
        except ValueError:
            totalAmount = 0

        try:
            tipAmount = float(request.form.get("tip_amount") or 0)
        except ValueError:
            tipAmount = 0

        category = request.form.get("category")

        try:
            next_row = len(sheet.col_values(1)) + 1
            sheet.update(f"A{next_row}:F{next_row}", [[timestamp, purchaseDate, itemDesc, totalAmount, tipAmount, category]])
            print(f"✅ Added to Google Sheet")
        except Exception as e:
            print(f"❌ Error updating sheet: {e}")

        return redirect("/")

    return render_template("index.html", today=today)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
