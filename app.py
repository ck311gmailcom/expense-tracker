from flask import Flask, render_template, request, redirect
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os, json

app = Flask(__name__)

# Set up Google Sheets
# Render & Google sheets using ckaminski311@gmail.com
#Set up PORT for render to use
app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(os.environ['GOOGLE_CREDS'])
creds = ServiceAccountCredentials.from_json_keyfile_name(creds_dict, scope)
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
            sheet.append_row([timestamp, purchaseDate, itemDesc, totalAmount, tipAmount, category])
            print("✅ Added to Google Sheet")
        except Exception as e:
            print(f"❌ Error updating sheet: {e}")

        return redirect("/")
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=False)