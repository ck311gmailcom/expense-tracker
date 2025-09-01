from flask import Flask, render_template, request, redirect
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os, json

app = Flask(__name__)

# Set up Google Sheets
# Render & Google sheets using ckaminski311@gmail.com
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
# Use the Render secret file path
secret_path = "/etc/secrets/GOOGLE_CREDS"  # use your secret file name
with open(secret_path) as f:
    creds_dict = json.load(f)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open("Official_Budget").worksheet("Expense Responses")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Generate timestamp on the server side
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        purchaseDate = request.form.get("purchase_date")
        itemDesc = request.form.get("item_description")
        totalAmount = request.form.get("total_amount")
        tipAmount = request.form.get("tip_amount")
        category = request.form.get("category")

        try:
            # Determine next empty row based on Column A
            next_row = len(sheet.col_values(1)) + 1
            # Update the row explicitly from Column A
            sheet.update(f"A{next_row}:F{next_row}", [[timestamp, purchaseDate, itemDesc, totalAmount, tipAmount, category]])            print(f"✅ Added to Google Sheet")
        
        except Exception as e:
            print(f"❌ Error updating sheet: {e}")

        return redirect("/")
    return render_template("index.html")

if __name__ == "__main__":
    app.run(
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 5000)),
    debug=False
    )