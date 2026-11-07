from flask import Flask, render_template, request, redirect
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os, json, re, logging, time

APP_VERSION = "3.0"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
TZ = pytz.timezone("America/New_York")

# ---------------------------------------------------------------------------
# Dropdown lists (kept in code on purpose). If you add/rename a row on the
# month tab, Funds tab or Real Estate tab, update the matching list here.
# ---------------------------------------------------------------------------
EXPENSE_CATEGORIES = [
    "Eating Out", "Doordash", "Groceries", "Going out/Bars/Drinks", "Entertainment",
    "Dates", "Gifts", "Clothes", "Personal Care", "Home Goods", "Pet Related",
    "Gas", "Travel/Uber", "Car Related",
    # Bills
    "Rent", "Utilities", "Car Insurance", "Student Loans",
    "Other",
]
SAVINGS_CATEGORIES = ["Investments", "Roth IRA", "Savings"]
FUNDS = ["Emergency Fund", "Vacation", "Real Estate Reserve", "Car", "Wedding Ring"]
ONE_TIME = "One-time"            # Paid From value for a big expense not tied to a fund

PROPERTY_NAME = "1453-1455"      # must match 'Real Estate' and 'Property Log' Property column
UNITS = ["1453", "1455", "Shared"]
PROPERTY_TYPES = ["Rent Received", "Mortgage Payment", "Expense", "Deposit Received", "Deposit Returned"]
SCHEDULE_E_CATEGORIES = [
    "Repairs", "Cleaning & Maintenance", "Supplies", "Utilities", "Insurance", "Taxes",
    "Capital Improvement", "Legal & Professional", "Management Fees", "Advertising",
    "Travel", "Other",
]

# Month-tab ranges read for the budget previews (Nov2026 layout)
EXPENSE_RANGE = "G11:I26"   # Category | Budget | Actual
BILL_RANGE = "B33:D37"      # Bill | Budget | Actual
SAVINGS_RANGE = "B20:D27"   # Goal | Planned | Actual
# Real Estate tab ranges
LEASE_RANGE = "B30:I39"     # Unit | Tenant | Start | End | Rent | Deposit | Owner-Occupied | Status
MORTGAGE_RANGE = "A12:I12"  # Property | Lender | Loan | Rate | Term | First Pmt | P&I | Escrow | Total
ESCROW_ROWS = (17, 26)      # Escrow History rows (A=Property, B=Effective, E=Monthly Escrow, G=Notes)


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


# Simple in-memory cache with TTL
_cache = {}

def cache_get(key, ttl, fetch_fn):
    """Return cached value if fresh, otherwise fetch and cache."""
    now_t = time.time()
    if key in _cache and now_t - _cache[key]['t'] < ttl:
        return _cache[key]['d']
    data = fetch_fn()
    _cache[key] = {'t': now_t, 'd': data}
    return data


# ---------------------------------------------------------------------------
# Google Sheets setup
# ---------------------------------------------------------------------------
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
secret_path = os.environ.get("GOOGLE_CREDS_PATH", "/etc/secrets/GOOGLE_CREDS")
with open(secret_path) as f:
    creds_dict = json.load(f)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
spreadsheet = client.open("Official_Budget")
sheet = spreadsheet.worksheet("Expense Responses")
income_sheet = spreadsheet.worksheet("Income")
property_sheet = spreadsheet.worksheet("Property Log")
re_sheet = spreadsheet.worksheet("Real Estate")


@app.route("/version")
def version():
    return f"v{APP_VERSION}"


# ---------------------------------------------------------------------------
# POST handlers
# ---------------------------------------------------------------------------
def handle_savings(form, timestamp):
    """Savings / fund contribution -> Expense Responses columns L-P."""
    row = [timestamp, form.get("contribute_date"), form.get("savings_description"),
           float(form.get("savings_amount")), form.get("savings_category")]
    try:
        next_row = len(sheet.col_values(12)) + 1  # Column L
        sheet.update(f"L{next_row}:P{next_row}", [row], value_input_option='USER_ENTERED')
        logger.info(f"Savings logged: {row}")
    except Exception as e:
        logger.error(f"Error writing savings: {e}")
    return "savings"


def handle_expense(form, timestamp):
    """Personal expense -> new row 2 of Expense Responses (columns A-J)."""
    category = form.get("category")
    other_category = form.get("other_category", "").strip()
    if category == "Other" and other_category:
        category = other_category

    subcategories = form.getlist("subcategories")
    other_text = form.get("other_subcategory", "").strip()
    if "Other" in subcategories:
        if other_text:
            subcategories[subcategories.index("Other")] = other_text
        else:
            subcategories.remove("Other")

    try:
        tip = float(form.get("tip_amount"))
    except (TypeError, ValueError):
        tip = 0.0

    paid_from = form.get("paid_from", "").strip()
    if paid_from not in FUNDS and paid_from != ONE_TIME:
        paid_from = ""  # blank = regular budget spending

    row = [timestamp, form.get("purchase_date"), form.get("item_description"),
           float(form.get("total_amount")), tip, category, ", ".join(subcategories),
           paid_from,
           '=IF(ISBLANK(E2),"",IFERROR(D2-E2,"N/A"))',
           '=IF(ISBLANK(E2),"",IFERROR(E2/(D2-E2),"N/A"))']
    try:
        # Insert at row 2 so newest expenses are on top. Month-tab formulas use
        # whole-column ranges, so this insert no longer shifts them.
        sheet.insert_rows([row], row=2, value_input_option='USER_ENTERED')
        logger.info(f"Expense logged: {row[:8]}")
    except Exception as e:
        logger.error(f"Error writing expense: {e}")
    return "expense"


def update_escrow_if_changed(new_escrow, effective_date, timestamp):
    """If the escrow on a mortgage payment differs from the current escrow on the
    Real Estate tab, add a dated row to Escrow History so future months use it."""
    try:
        mortgage = get_property_data()["mortgage"]
        current = mortgage.get("escrow", 0.0)
        if abs(new_escrow - current) < 0.005:
            return
        first, last = ESCROW_ROWS
        col_a = re_sheet.get(f"A{first}:A{last}")
        filled = len([r for r in col_a if r and str(r[0]).strip()])
        target = first + filled
        if target > last:
            logger.error("Escrow History is full; add rows on the Real Estate tab.")
            return
        re_sheet.batch_update([
            {"range": f"A{target}:B{target}", "values": [[PROPERTY_NAME, effective_date]]},
            {"range": f"E{target}", "values": [[new_escrow]]},
            {"range": f"G{target}", "values": [[f"Updated from app {timestamp} (was ${current:,.2f})"]]},
        ], value_input_option='USER_ENTERED')
        logger.info(f"Escrow updated {current} -> {new_escrow} effective {effective_date}")
    except Exception as e:
        logger.error(f"Error updating escrow: {e}")


def handle_property(form, timestamp):
    """Real estate entry -> appended to Property Log (columns A-K)."""
    ptype = form.get("property_type")
    if ptype not in PROPERTY_TYPES:
        ptype = "Expense"
    unit = form.get("unit", "Shared")
    if unit not in UNITS:
        unit = "Shared"
    if ptype == "Mortgage Payment":
        unit = "Shared"

    category = form.get("re_category", "") if ptype == "Expense" else ""
    paid_from = form.get("re_paid_from", "") if ptype == "Expense" else ""
    if paid_from not in FUNDS and paid_from != ONE_TIME:
        paid_from = ""
    receipt = form.get("receipt_kept") == "on"
    amount = float(form.get("re_amount"))
    date = form.get("re_date")
    description = form.get("re_description", "").strip() or ptype

    row = [timestamp, date, PROPERTY_NAME, unit, ptype, category, description,
           amount, receipt, paid_from, form.get("re_notes", "").strip()]
    try:
        property_sheet.append_row(row, value_input_option='USER_ENTERED', table_range="A1")
        logger.info(f"Property entry logged: {row}")
    except Exception as e:
        logger.error(f"Error writing property entry: {e}")

    if ptype == "Mortgage Payment" and form.get("escrow_amount"):
        update_escrow_if_changed(parse_number(form.get("escrow_amount")), date, timestamp)
    return "property"


# ---------------------------------------------------------------------------
# GET data loaders
# ---------------------------------------------------------------------------
def get_property_data():
    """Leases (for rent prefill) and current mortgage numbers. Cached 30 min."""
    def _fetch():
        out = {"leases": {}, "mortgage": {}}
        try:
            leases, mort = re_sheet.batch_get([LEASE_RANGE, MORTGAGE_RANGE],
                                              value_render_option='UNFORMATTED_VALUE')
            for r in leases:
                r = list(r) + [""] * (8 - len(r))
                unit = str(r[0]).strip()
                if not unit or r[7] != "Active":
                    continue
                out["leases"][unit] = {"tenant": r[1], "rent": parse_number(r[4]),
                                       "deposit": parse_number(r[5])}
            if mort:
                m = list(mort[0]) + [""] * (9 - len(mort[0]))
                out["mortgage"] = {"pi": round(parse_number(m[6]), 2),
                                   "escrow": round(parse_number(m[7]), 2),
                                   "total": round(parse_number(m[8]), 2)}
        except Exception as e:
            logger.error(f"Error reading Real Estate tab: {e}")
        return out
    return cache_get('property', 1800, _fetch)


def get_budgets(now):
    """Budget/actual by category for this month and last month (cached 5 min)."""
    month_abbrevs = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    prev_month = now.month - 1 if now.month > 1 else 12
    prev_year = now.year if now.month > 1 else now.year - 1
    months = [(now.month, now.year), (prev_month, prev_year)]

    def _fetch():
        bd, sbd = {}, {}
        for m, y in months:
            tab = f"{month_abbrevs[m - 1]}{y}"
            try:
                ws = spreadsheet.worksheet(tab)
                exp, bills, sav = ws.batch_get([EXPENSE_RANGE, BILL_RANGE, SAVINGS_RANGE],
                                               value_render_option='UNFORMATTED_VALUE')
                month_budget = {}
                for row in list(exp) + list(bills):
                    if len(row) >= 2 and row[0]:
                        month_budget[str(row[0]).strip()] = {
                            "budgeted": parse_number(row[1]),
                            "actual": parse_number(row[2]) if len(row) > 2 else 0}
                bd[tab] = month_budget
                month_sav = {}
                for row in sav:
                    if len(row) >= 1 and row[0]:
                        month_sav[str(row[0]).strip()] = {
                            "expected": parse_number(row[1]) if len(row) > 1 else 0,
                            "actual": parse_number(row[2]) if len(row) > 2 else 0}
                sbd[tab] = month_sav
            except gspread.exceptions.WorksheetNotFound:
                logger.info(f"No tab {tab} yet")
            except Exception as e:
                logger.error(f"Error loading budget tab {tab}: {e}")
        return bd, sbd
    return cache_get('budgets', 300, _fetch)


def recent_rows(values, date_col, amount_col, cat_col, n=5, start=1, require_col=None):
    """Return the n most recent rows (by date text, newest first)."""
    rows = [r for r in values[start:] if len(r) > date_col and r[date_col]
            and (require_col is None or (len(r) > require_col and r[require_col]))]
    rows.sort(key=lambda r: r[date_col], reverse=True)
    return [{"date": r[date_col],
             "amount": parse_number(r[amount_col]) if len(r) > amount_col else 0,
             "category": r[cat_col] if len(r) > cat_col else ""} for r in rows[:n]]


# ---------------------------------------------------------------------------
# Main route
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    # POST first: skips all reads
    if request.method == "POST":
        _cache.pop('budgets', None)
        timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        form_type = request.form.get("form_type")
        if form_type == "savings":
            done = handle_savings(request.form, timestamp)
        elif form_type == "property":
            _cache.pop('property', None)
            done = handle_property(request.form, timestamp)
        else:
            done = handle_expense(request.form, timestamp)
        return redirect(f"/?submitted={done}")

    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")

    hourly_rate = None
    try:
        hourly_rate = cache_get('hourly_rate', 1800,
                                lambda: parse_number(income_sheet.acell('L3').value))
    except Exception as e:
        logger.error(f"Error reading hourly rate: {e}")

    budget_data, savings_budget_data = get_budgets(now)
    property_data = get_property_data()

    recent_expenses, recent_savings, recent_property = [], [], []
    try:
        vals = sheet.get_all_values()
        if vals:
            headers = [h.strip().lower() for h in vals[0]]
            date_col = next((i for i, h in enumerate(headers) if 'purchase' in h and 'date' in h), 1)
            amount_col = next((i for i, h in enumerate(headers) if 'total' in h and 'amount' in h), 3)
            category_col = next((i for i, h in enumerate(headers) if h == 'category'), 5)
            recent_expenses = recent_rows(vals, date_col, amount_col, category_col)
            recent_savings = recent_rows(vals, 12, 14, 15, require_col=12)
    except Exception as e:
        logger.error(f"Error reading recent expenses/savings: {e}")
    try:
        pvals = property_sheet.get_all_values()
        prow = sorted([r for r in pvals[1:] if len(r) > 7 and r[1]],
                      key=lambda r: r[1], reverse=True)[:5]
        recent_property = [{"date": r[1], "amount": parse_number(r[7]),
                            "category": f"{r[3]} · {r[4]}"} for r in prow]
    except Exception as e:
        logger.error(f"Error reading property log: {e}")

    return render_template(
        "index.html", today=today, submitted_type=request.args.get("submitted"),
        recent_expenses=recent_expenses, recent_savings=recent_savings,
        recent_property=recent_property, hourly_rate=hourly_rate,
        budget_data=budget_data, savings_budget_data=savings_budget_data,
        property_data=property_data,
        expense_categories=EXPENSE_CATEGORIES, savings_categories=SAVINGS_CATEGORIES,
        funds=FUNDS, one_time=ONE_TIME, units=UNITS, property_types=PROPERTY_TYPES,
        schedule_e=SCHEDULE_E_CATEGORIES, property_name=PROPERTY_NAME)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
