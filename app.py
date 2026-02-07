from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os, json, re, logging

APP_VERSION = "2.1"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def parse_number(val):
    """Parse a number from various formats: 500, '500', '$500.00', '1,234.56'"""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r'[^\d.\-]', '', str(val))
    try:
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0

# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
secret_path = "/etc/secrets/GOOGLE_CREDS"
with open(secret_path) as f:
    creds_dict = json.load(f)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
spreadsheet = client.open("Official_Budget")
sheet = spreadsheet.worksheet("Expense Responses")
income_sheet = spreadsheet.worksheet("Income")

@app.route("/version")
def version():
    return f"v{APP_VERSION}"

@app.route("/", methods=["GET", "POST"])
def index():
    tz = pytz.timezone("America/New_York")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    submitted_type = request.args.get("submitted")  # 'expense' or 'savings' or None

    # Read hourly rate from Income tab (L3 = Total Active Salary Rate)
    hourly_rate = None
    try:
        hourly_rate = parse_number(income_sheet.acell('L3').value)
    except Exception as e:
        logger.error(f"Error reading hourly rate: {e}")

    # Read budget data from current + previous month tabs
    # Structure: {"Feb2026": {category: {budgeted, actual}}, "Jan2026": {...}}
    budget_data = {}
    savings_budget_data = {}
    month_abbrevs = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    category_map = {"Travel": "Travel/Uber"}

    now = datetime.now(tz)
    # Build list of months to load: current + previous
    months_to_load = []
    months_to_load.append((now.month, now.year))
    prev_month = now.month - 1 if now.month > 1 else 12
    prev_year = now.year if now.month > 1 else now.year - 1
    months_to_load.append((prev_month, prev_year))

    for m, y in months_to_load:
        tab_name = f"{month_abbrevs[m - 1]}{y}"
        try:
            budget_ws = spreadsheet.worksheet(tab_name)
            logger.info(f"Loaded budget tab: {tab_name}")

            # Expense categories: G7:I24, Bill categories: G29:I32
            # Use UNFORMATTED_VALUE to get raw numbers (avoids "$500.00" strings)
            expense_range = budget_ws.get('G7:I24', value_render_option='UNFORMATTED_VALUE')
            bill_range = budget_ws.get('G29:I32', value_render_option='UNFORMATTED_VALUE')

            month_budget = {}
            for row in expense_range + bill_range:
                if len(row) >= 2 and row[0]:
                    cat_name = str(row[0]).strip()
                    app_cat_name = category_map.get(cat_name, cat_name)
                    budgeted = parse_number(row[1]) if len(row) > 1 else 0
                    actual = parse_number(row[2]) if len(row) > 2 else 0
                    month_budget[app_cat_name] = {"budgeted": budgeted, "actual": actual}
            budget_data[tab_name] = month_budget
            logger.info(f"Budget categories for {tab_name}: {list(month_budget.keys())}")

            # Savings goal data: Expected Motion section B30:D32
            month_savings = {}
            savings_range = budget_ws.get('B30:D32', value_render_option='UNFORMATTED_VALUE')
            for row in savings_range:
                if len(row) >= 2 and row[0]:
                    cat_name = str(row[0]).strip()
                    expected = parse_number(row[1]) if len(row) > 1 else 0
                    actual = parse_number(row[2]) if len(row) > 2 else 0
                    month_savings[cat_name] = {"expected": expected, "actual": actual}
            savings_budget_data[tab_name] = month_savings
            logger.info(f"Savings categories for {tab_name}: {list(month_savings.keys())}")
        except Exception as e:
            logger.error(f"Error loading budget tab {tab_name}: {e}")

    # Read recent expenses from Google Sheets (sorted by purchase date)
    # Uses header row to find columns dynamically
    recent_expenses = []
    recent_savings = []
    try:
        all_values = sheet.get_all_values()
        if all_values:
            headers = [h.strip().lower() for h in all_values[0]]
            logger.info(f"Sheet headers: {headers[:10]}")

            # Find expense column indices from headers
            date_col = next((i for i, h in enumerate(headers) if 'purchase' in h and 'date' in h), 1)
            amount_col = next((i for i, h in enumerate(headers) if 'total' in h and 'amount' in h), 3)
            category_col = next((i for i, h in enumerate(headers) if h == 'category'), 5)
            logger.info(f"Column indices - date: {date_col}, amount: {amount_col}, category: {category_col}")

            data_rows = all_values[1:]
            data_rows.sort(key=lambda r: r[date_col] if len(r) > date_col and r[date_col] else "", reverse=True)
            for row in data_rows[:5]:
                amt_raw = row[amount_col] if len(row) > amount_col else "0"
                amt = parse_number(amt_raw)
                recent_expenses.append({
                    "date": row[date_col] if len(row) > date_col else "",
                    "amount": amt,
                    "category": row[category_col] if len(row) > category_col else ""
                })

            # Read recent savings from columns L-P
            # L=Timestamp(11), M=Contribute Date(12), N=Description(13), O=Amount(14), P=Category(15)
            savings_rows = []
            for row in all_values[1:]:
                if len(row) > 14 and row[12]:  # col M (contribute date) exists
                    savings_rows.append(row)
            savings_rows.sort(key=lambda r: r[12] if r[12] else "", reverse=True)
            for row in savings_rows[:5]:
                amt_raw = row[14] if len(row) > 14 else "0"
                amt = parse_number(amt_raw)
                recent_savings.append({
                    "date": row[12],
                    "amount": amt,
                    "category": row[15] if len(row) > 15 else ""
                })
    except Exception as e:
        logger.error(f"Error reading recent expenses/savings: {e}")

    if request.method == "POST":
        form_type = request.form.get("form_type")
        timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        if form_type == "savings":
            contributeDate = request.form.get("contribute_date")
            description = request.form.get("savings_description")
            savingsAmount = float(request.form.get("savings_amount"))
            savingsCategory = request.form.get("savings_category")

            # Google Sheets update - writes to columns L-P in Expense Responses
            try:
                next_row = len(sheet.col_values(12)) + 1  # Column L
                sheet.update(f"L{next_row}:P{next_row}",
                             [[timestamp, contributeDate, description, savingsAmount, savingsCategory]],
                             value_input_option='USER_ENTERED')
                print(f"✅ Savings added to Google Sheet")
            except Exception as e:
                print(f"❌ Error updating sheet: {e}")

            print(f"✅ Savings logged: {contributeDate} | {description} | ${savingsAmount} | {savingsCategory}")
            return redirect("/?submitted=savings")

        else:
            purchaseDate = request.form.get("purchase_date")
            itemDesc = request.form.get("item_description")
            totalAmount = float(request.form.get("total_amount"))
            category = request.form.get("category")
            other_category = request.form.get("other_category", "").strip()

            # Handle "Other" category
            if category == "Other" and other_category:
                category = other_category
            elif category == "Other" and not other_category:
                category = "Other"

            subcategories = request.form.getlist("subcategories")
            other_text = request.form.get("other_subcategory", "").strip()

            # Handle "Other" subcategory
            if "Other" in subcategories and other_text:
                subcategories[subcategories.index("Other")] = other_text
            elif "Other" in subcategories and not other_text:
                subcategories.remove("Other")

            subcategory_str = ", ".join(subcategories) if subcategories else ""

            try:
                tipAmount = float(request.form.get("tip_amount"))
            except (TypeError, ValueError):
                tipAmount = 0.0

            # Google Sheets update - Insert at row 2 so newest expenses are always at the top
            try:
                sheet.insert_rows(
                    [[timestamp, purchaseDate, itemDesc, totalAmount, tipAmount, category, subcategory_str,
                      '', '=IF(ISBLANK(E2),"",IFERROR(D2-E2,"N/A"))', '=IF(ISBLANK(E2),"",IFERROR(E2/(D2-E2),"N/A"))']],
                    row=2,
                    value_input_option='USER_ENTERED'
                )
                print(f"✅ Added to Google Sheet")
            except Exception as e:
                print(f"❌ Error updating sheet: {e}")

            print(f"✅ Expense logged: {purchaseDate} | {itemDesc} | ${totalAmount} | Tip: ${tipAmount} | {category} | {subcategory_str}")
            return redirect("/?submitted=expense")

    return render_template("index.html", today=today, submitted_type=submitted_type,
                           recent_expenses=recent_expenses, recent_savings=recent_savings,
                           hourly_rate=hourly_rate,
                           budget_data=budget_data, savings_budget_data=savings_budget_data)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
