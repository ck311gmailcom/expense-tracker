from flask import Flask, render_template, request, redirect
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os, json

app = Flask(__name__)

# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(os.environ['GOOGLE_CREDS'])
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open("Official_Budget").worksheet("Expense Responses")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Server-side timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Get form values
        purchaseDate = request.form.get("purchase_date")
        itemDesc = request.form.get("item_description")

        # Safely convert numeric inputs
        try:
            totalAmount = float(request.form.get("total_amount") or 0)
        except ValueError:
            totalAmount = 0

        try:
            tipAmount = float(request.form.get("tip_amount") or 0)
        except ValueError:
            tipAmount = 0

        category = request.form.get("category")

        # Debug prints
        print("Submitting expense:", timestamp, purchaseDate, itemDesc, totalAmount, tipAmount, category)

        # Update the next empty row explicitly in columns A-F
        try:
            next_row = len(sheet.col_values(1)) + 1
            sheet.update(
                f"A{next_row}:F{next_row}",
                [[timestamp, purchaseDate, itemDesc, totalAmount, tipAmount, category]]
            )
            print(f"✅ Added to Google Sheet row {next_row}")
        except Exception as e:
            print(f"❌ Error updating sheet: {e}")

        return redirect("/")

    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
