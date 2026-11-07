from flask import Flask, render_template, request, redirect
from datetime import datetime
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os, json, re, logging, time

APP_VERSION = "3.2"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
TZ = pytz.timezone("America/New_York")
MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# ---------------------------------------------------------------------------
# Lists. Expense categories, funds and accounts are read live from the sheet
# (month tab, Funds tab, Accounts tab). The lists below are only fallbacks if
# a read fails, so the app keeps working.
# ---------------------------------------------------------------------------
FALLBACK_EXPENSE_CATEGORIES = [
    "Eating Out", "Doordash", "Groceries", "Going out/Bars/Drinks", "Entertainment",
    "Dates", "Gifts", "Clothes", "Personal Care", "Home Goods", "Pet Related",
    "Gas", "Travel/Uber", "Car Related", "Rent", "Utilities", "Car Insurance",
    "Student Loans", "Other",
]
FALLBACK_FUNDS = ["Emergency Fund", "Vacation", "Real Estate Reserve", "Car", "Wedding Ring"]
SKIP_CATEGORIES = {"Subscriptions", "Not in a category", "Total", ""}  # never offered in the app
ONE_TIME = "One-time"            # Paid From value for a big expense not tied to a fund

MOVE_TYPES = ["Transfer", "Credit Card Payment", "Fund Move"]
CASH_TYPES = {"Checking", "Savings", "Credit Card"}
LIABILITY_TYPES = {"Credit Card", "Loan", "Mortgage"}

PROPERTY_NAME = "1453-1455"      # must match 'Real Estate' and 'Property Log' Property column
UNITS = ["1453", "1455", "Shared"]
PROPERTY_TYPES = ["Rent Received", "Mortgage Payment", "Expense", "Deposit Received", "Deposit Returned"]
SCHEDULE_E_CATEGORIES = [
    "Repairs", "Cleaning & Maintenance", "Supplies", "Utilities", "Insurance", "Taxes",
    "Capital Improvement", "Legal & Professional", "Management Fees", "Advertising",
    "Travel", "Other",
]

# Month-tab ranges (Nov2026 layout)
EXPENSE_RANGE = "G11:I26"   # Category | Budget | Actual
BILL_RANGE = "B33:D37"      # Bill | Budget | Actual
SAVINGS_RANGE = "B20:D27"   # Goal | Planned | Actual
ACCOUNT_RANGE = "B55:G69"   # Account | Type | Start | Expected | (gap) | Actual End
# Other tabs
FUNDS_RANGE = "Funds!A5:H14"          # Fund ... H = Balance
ACCOUNTS_RANGE = "Accounts!A5:D19"    # Account | Type | Institution | Linked Category
LEASE_RANGE = "B30:J39"     # Unit | Tenant | Start | End | Rent | Due Day | Deposit | Owner-Occ | Status
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


def is_blank(val):
    return val is None or str(val).strip() == ""


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
moves_sheet = spreadsheet.worksheet("Money Moves")


@app.route("/version")
def version():
    return f"v{APP_VERSION}"


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
def month_tabs(now):
    prev_m = now.month - 1 if now.month > 1 else 12
    prev_y = now.year if now.month > 1 else now.year - 1
    return [f"{MONTH_ABBR[now.month - 1]}{now.year}", f"{MONTH_ABBR[prev_m - 1]}{prev_y}"]


def get_month_data(now):
    """Budget/actual by category, savings goals and account rows for this
    month and last month. Cached 5 min, cleared after any submit."""
    def _fetch():
        out = {"budgets": {}, "savings": {}, "accounts": {}, "categories": [], "bills": []}
        for i, tab in enumerate(month_tabs(now)):
            try:
                ws = spreadsheet.worksheet(tab)
                exp, bills, sav, accts = ws.batch_get(
                    [EXPENSE_RANGE, BILL_RANGE, SAVINGS_RANGE, ACCOUNT_RANGE],
                    value_render_option='UNFORMATTED_VALUE')
            except gspread.exceptions.WorksheetNotFound:
                logger.info(f"No tab {tab} yet")
                continue
            except Exception as e:
                logger.error(f"Error loading month tab {tab}: {e}")
                continue
            budget = {}
            for row in list(exp) + list(bills):
                if row and not is_blank(row[0]):
                    budget[str(row[0]).strip()] = {
                        "budgeted": parse_number(row[1]) if len(row) > 1 else 0,
                        "actual": parse_number(row[2]) if len(row) > 2 else 0}
            out["budgets"][tab] = budget
            out["savings"][tab] = {
                str(r[0]).strip(): {"expected": parse_number(r[1]) if len(r) > 1 else 0,
                                    "actual": parse_number(r[2]) if len(r) > 2 else 0}
                for r in sav if r and not is_blank(r[0])}
            out["accounts"][tab] = [
                {"name": str(r[0]).strip(), "type": r[1] if len(r) > 1 else "",
                 "start": r[2] if len(r) > 2 else "", "actual": r[5] if len(r) > 5 else ""}
                for r in accts if r and not is_blank(r[0])]
            if i == 0 or not out["categories"]:
                out["categories"] = [str(r[0]).strip() for r in exp if r and str(r[0]).strip() not in SKIP_CATEGORIES]
                out["bills"] = [str(r[0]).strip() for r in bills if r and str(r[0]).strip() not in SKIP_CATEGORIES]
        return out
    return cache_get('month', 300, _fetch)


def get_lists():
    """Funds (with balances) and accounts (with type and linked goal). Cached 5 min."""
    def _fetch():
        out = {"funds": [], "accounts": []}
        try:
            res = spreadsheet.values_batch_get([FUNDS_RANGE, ACCOUNTS_RANGE],
                                               params={"valueRenderOption": "UNFORMATTED_VALUE"})
            vr = res.get("valueRanges", [])
            funds = vr[0].get("values", []) if vr else []
            accts = vr[1].get("values", []) if len(vr) > 1 else []
            out["funds"] = [{"name": str(r[0]).strip(),
                             "balance": parse_number(r[7]) if len(r) > 7 else 0.0}
                            for r in funds if r and not is_blank(r[0])]
            out["accounts"] = [{"name": str(r[0]).strip(), "type": r[1] if len(r) > 1 else "",
                                "goal": str(r[3]).strip() if len(r) > 3 else ""}
                               for r in accts if r and not is_blank(r[0])]
        except Exception as e:
            logger.error(f"Error reading Funds/Accounts: {e}")
        if not out["funds"]:
            out["funds"] = [{"name": f, "balance": None} for f in FALLBACK_FUNDS]
        return out
    return cache_get('lists', 300, _fetch)


def get_property_data():
    """Leases (for rent prefill) and current mortgage numbers. Cached 30 min."""
    def _fetch():
        out = {"leases": {}, "mortgage": {}}
        try:
            leases, mort = re_sheet.batch_get([LEASE_RANGE, MORTGAGE_RANGE],
                                              value_render_option='UNFORMATTED_VALUE')
            for r in leases:
                r = list(r) + [""] * (9 - len(r))
                unit = str(r[0]).strip()
                if not unit or r[8] != "Active":
                    continue
                out["leases"][unit] = {"tenant": r[1], "rent": parse_number(r[4]),
                                       "due_day": r[5], "deposit": parse_number(r[6])}
            if mort:
                m = list(mort[0]) + [""] * (9 - len(mort[0]))
                out["mortgage"] = {"pi": round(parse_number(m[6]), 2),
                                   "escrow": round(parse_number(m[7]), 2),
                                   "total": round(parse_number(m[8]), 2)}
        except Exception as e:
            logger.error(f"Error reading Real Estate tab: {e}")
        return out
    return cache_get('property', 1800, _fetch)


def to_date_key(val):
    """'2026-11-05' or a sheet serial -> 'YYYY-MM' for month matching."""
    s = str(val).strip()
    m = re.match(r"(\d{4})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def account_balances(now, month_data, moves_rows):
    """Last known balance per account: this month's Actual End if entered,
    otherwise Start of Month (= last month's Actual End) adjusted for money
    moves logged this month. Card spending isn't tracked per card, so card
    balances are approximate until month-end actuals are entered."""
    tab = month_tabs(now)[0]
    this_month = now.strftime("%Y-%m")
    net = {}
    for r in moves_rows:
        if len(r) < 6 or to_date_key(r[1]) != this_month:
            continue
        amt = parse_number(r[5])
        net.setdefault(r[3], {"in": 0.0, "out": 0.0})["out"] += amt
        net.setdefault(r[4], {"in": 0.0, "out": 0.0})["in"] += amt
    out = {}
    for a in month_data["accounts"].get(tab, []):
        moves = net.get(a["name"], {"in": 0.0, "out": 0.0})
        sign = -1 if a["type"] in LIABILITY_TYPES else 1
        if not is_blank(a["actual"]):
            out[a["name"]] = {"balance": parse_number(a["actual"]), "note": f"{tab} month-end"}
        elif not is_blank(a["start"]):
            bal = parse_number(a["start"]) + sign * (moves["in"] - moves["out"])
            has_moves = moves["in"] or moves["out"]
            out[a["name"]] = {"balance": round(bal, 2),
                              "note": f"start of {tab}" + (" + moves" if has_moves else "")}
    return out


def recent_rows(values, date_col, amount_col, label_fn, n=5, start=1, require_col=None):
    """Return the n most recent rows (by date text, newest first)."""
    rows = [r for r in values[start:] if len(r) > date_col and r[date_col]
            and (require_col is None or (len(r) > require_col and r[require_col]))]
    rows.sort(key=lambda r: r[date_col], reverse=True)
    return [{"date": r[date_col],
             "amount": parse_number(r[amount_col]) if len(r) > amount_col else 0,
             "category": label_fn(r)} for r in rows[:n]]


# ---------------------------------------------------------------------------
# POST handlers
# ---------------------------------------------------------------------------
def handle_expense(form, timestamp, funds):
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
    if paid_from not in funds and paid_from != ONE_TIME:
        paid_from = ""  # blank = regular budget spending

    row = [timestamp, form.get("purchase_date"), form.get("item_description"),
           float(form.get("total_amount")), tip, category, ", ".join(subcategories),
           paid_from,
           '=IF(ISBLANK(E2),"",IFERROR(D2-E2,"N/A"))',
           '=IF(ISBLANK(E2),"",IFERROR(E2/(D2-E2),"N/A"))']
    try:
        # Newest on top. Month-tab formulas use whole columns, so this insert can't shift them.
        sheet.insert_rows([row], row=2, value_input_option='USER_ENTERED')
        logger.info(f"Expense logged: {row[:8]}")
    except Exception as e:
        logger.error(f"Error writing expense: {e}")
    return "expense"


def handle_move(form, timestamp, valid_names):
    """Transfer / card payment / fund move -> appended to Money Moves (A-H)."""
    mtype = form.get("move_type") if form.get("move_type") in MOVE_TYPES else "Transfer"
    src, dst = form.get("move_from", ""), form.get("move_to", "")
    if src not in valid_names or dst not in valid_names or src == dst:
        logger.error(f"Rejected move: from={src!r} to={dst!r}")
        return "move_error"
    row = [timestamp, form.get("move_date"), mtype, src, dst, float(form.get("move_amount")),
           form.get("move_description", "").strip() or f"{mtype}: {src} to {dst}",
           form.get("move_notes", "").strip()]
    try:
        moves_sheet.append_row(row, value_input_option='USER_ENTERED', table_range="A1")
        logger.info(f"Money move logged: {row}")
    except Exception as e:
        logger.error(f"Error writing money move: {e}")
    return "move"


def update_escrow_if_changed(new_escrow, effective_date, timestamp):
    """If the escrow on a mortgage payment differs from the current escrow on the
    Real Estate tab, add a dated row to Escrow History so future months use it."""
    try:
        current = get_property_data()["mortgage"].get("escrow", 0.0)
        if abs(new_escrow - current) < 0.005:
            return
        first, last = ESCROW_ROWS
        col_a = re_sheet.get(f"A{first}:A{last}")
        target = first + len([r for r in col_a if r and str(r[0]).strip()])
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


def handle_property(form, timestamp, funds):
    """Real estate entry -> appended to Property Log (columns A-K)."""
    ptype = form.get("property_type") if form.get("property_type") in PROPERTY_TYPES else "Expense"
    unit = form.get("unit", "Shared") if form.get("unit") in UNITS else "Shared"
    if ptype == "Mortgage Payment":
        unit = "Shared"
    category = form.get("re_category", "") if ptype == "Expense" else ""
    paid_from = form.get("re_paid_from", "") if ptype == "Expense" else ""
    if paid_from not in funds:
        paid_from = ""
    date = form.get("re_date")
    row = [timestamp, date, PROPERTY_NAME, unit, ptype, category,
           form.get("re_description", "").strip() or ptype, float(form.get("re_amount")),
           form.get("receipt_kept") == "on", paid_from, form.get("re_notes", "").strip()]
    try:
        property_sheet.append_row(row, value_input_option='USER_ENTERED', table_range="A1")
        logger.info(f"Property entry logged: {row}")
    except Exception as e:
        logger.error(f"Error writing property entry: {e}")
    if ptype == "Mortgage Payment" and form.get("escrow_amount"):
        update_escrow_if_changed(parse_number(form.get("escrow_amount")), date, timestamp)
    return "property"


# ---------------------------------------------------------------------------
# Main route
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    now = datetime.now(TZ)

    if request.method == "POST":
        lists = get_lists()
        fund_names = [f["name"] for f in lists["funds"]]
        _cache.pop('month', None)
        _cache.pop('lists', None)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        form_type = request.form.get("form_type")
        if form_type == "move":
            valid = fund_names + [a["name"] for a in lists["accounts"]]
            done = handle_move(request.form, timestamp, valid)
        elif form_type == "savings":
            # Old Savings form (cached page). Savings now go through Move Money.
            done = "move_error"
        elif form_type == "property":
            _cache.pop('property', None)
            done = handle_property(request.form, timestamp, fund_names)
        else:
            done = handle_expense(request.form, timestamp, fund_names)
        return redirect(f"/?submitted={done}")

    today = now.strftime("%Y-%m-%d")
    hourly_rate = None
    try:
        hourly_rate = cache_get('hourly_rate', 1800,
                                lambda: parse_number(income_sheet.acell('L3').value))
    except Exception as e:
        logger.error(f"Error reading hourly rate: {e}")

    month_data = get_month_data(now)
    lists = get_lists()
    property_data = get_property_data()
    categories = (month_data["categories"] + month_data["bills"]) or FALLBACK_EXPENSE_CATEGORIES
    if "Other" in categories:  # keep "Other" last
        categories = [c for c in categories if c != "Other"] + ["Other"]

    recent_expenses, recent_moves, recent_property, moves_rows = [], [], [], []
    try:
        vals = sheet.get_all_values()
        if vals:
            headers = [h.strip().lower() for h in vals[0]]
            date_col = next((i for i, h in enumerate(headers) if 'purchase' in h and 'date' in h), 1)
            amount_col = next((i for i, h in enumerate(headers) if 'total' in h and 'amount' in h), 3)
            cat_col = next((i for i, h in enumerate(headers) if h == 'category'), 5)
            recent_expenses = recent_rows(vals, date_col, amount_col, lambda r: r[cat_col] if len(r) > cat_col else "")
    except Exception as e:
        logger.error(f"Error reading recent expenses: {e}")
    try:
        moves_rows = moves_sheet.get_all_values()[1:]
        recent_moves = recent_rows([[]] + moves_rows, 1, 5,
                                   lambda r: f"{r[3]} → {r[4]}" if len(r) > 4 else "")
    except Exception as e:
        logger.error(f"Error reading money moves: {e}")
    try:
        pvals = property_sheet.get_all_values()
        recent_property = recent_rows(pvals, 1, 7, lambda r: f"{r[3]} · {r[4]}")
    except Exception as e:
        logger.error(f"Error reading property log: {e}")

    balances = account_balances(now, month_data, moves_rows)
    for f in lists["funds"]:
        if f["balance"] is not None:
            balances[f["name"]] = {"balance": f["balance"], "note": "fund balance"}

    return render_template(
        "index.html", today=today, submitted_type=request.args.get("submitted"),
        recent_expenses=recent_expenses, recent_moves=recent_moves,
        recent_property=recent_property, hourly_rate=hourly_rate,
        budget_data=month_data["budgets"], savings_budget_data=month_data["savings"],
        property_data=property_data, balances=balances,
        expense_categories=categories, funds=[f["name"] for f in lists["funds"]],
        accounts=lists["accounts"], move_types=MOVE_TYPES, one_time=ONE_TIME,
        units=UNITS, property_types=PROPERTY_TYPES, schedule_e=SCHEDULE_E_CATEGORIES,
        property_name=PROPERTY_NAME)


# ---------------------------------------------------------------------------
# Insights page (interactive Plotly charts)
# ---------------------------------------------------------------------------
NEW_LAYOUT_START = (2026, 11)   # month tabs before Nov2026 use the old layout
INSIGHT_RANGES = {
    "income": "B11:D15", "savings": "B20:D27", "expenses": "G11:I26", "bills": "B33:D37",
    "onetime": "J37:J38", "realestate": "B43:D49", "summary": "G43:I49",
}


def list_month_tabs():
    """Month tabs (MonYYYY) on the current layout, newest first. Cached 10 min."""
    def _fetch():
        tabs = []
        for ws in spreadsheet.worksheets():
            m = re.fullmatch(r"([A-Z][a-z]{2})(\d{4})", ws.title)
            if m and m.group(1) in MONTH_ABBR:
                y, mo = int(m.group(2)), MONTH_ABBR.index(m.group(1)) + 1
                if (y, mo) >= NEW_LAYOUT_START:
                    tabs.append(((y, mo), ws.title))
        return [t for _, t in sorted(tabs, reverse=True)]
    return cache_get('month_list', 600, _fetch)


def get_insights(tab):
    """Everything the Insights charts need for one month, as planned/actual pairs."""
    def _fetch():
        ws = spreadsheet.worksheet(tab)
        keys = list(INSIGHT_RANGES)
        vals = dict(zip(keys, ws.batch_get([INSIGHT_RANGES[k] for k in keys],
                                           value_render_option='UNFORMATTED_VALUE')))

        def rows(block, plan_col=1, act_col=2):
            out = []
            for r in vals[block]:
                if not r or is_blank(r[0]) or str(r[0]).strip() in SKIP_CATEGORIES:
                    continue
                out.append({"name": str(r[0]).strip(),
                            "planned": parse_number(r[plan_col]) if len(r) > plan_col else 0.0,
                            "actual": parse_number(r[act_col]) if len(r) > act_col else 0.0})
            return out

        expenses = []
        for r in vals["expenses"]:   # keep Subscriptions here (it's a real cost)
            if r and not is_blank(r[0]) and str(r[0]).strip() not in {"Not in a category", "Total"}:
                expenses.append({"name": str(r[0]).strip(),
                                 "planned": parse_number(r[1]) if len(r) > 1 else 0.0,
                                 "actual": parse_number(r[2]) if len(r) > 2 else 0.0})
        unmatched = next((r for r in vals["expenses"] if r and str(r[0]).strip() == "Not in a category"), None)
        if unmatched and len(unmatched) > 2 and parse_number(unmatched[2]):
            expenses.append({"name": "Not in a category", "planned": 0.0, "actual": parse_number(unmatched[2])})
        onetime = vals["onetime"]
        one_nf = parse_number(onetime[1][0]) if len(onetime) > 1 and onetime[1] else 0.0

        re_rows = {str(r[0]).strip(): r for r in vals["realestate"] if r and not is_blank(r[0])}
        def re_val(prefix, col):
            r = next((v for k, v in re_rows.items() if k.startswith(prefix)), None)
            return parse_number(r[col]) if r and len(r) > col else 0.0
        rent = [{"name": k, "planned": parse_number(v[1]) if len(v) > 1 else 0.0,
                 "actual": parse_number(v[2]) if len(v) > 2 else 0.0}
                for k, v in re_rows.items() if k.startswith("Rent")]
        property_out = [
            {"name": "Mortgage payment", "planned": re_val("Mortgage", 1), "actual": re_val("Mortgage", 2)},
            {"name": "Property expenses", "planned": re_val("Other property", 1), "actual": re_val("Other property", 2)},
        ]
        summary = {str(r[0]).strip(): r for r in vals["summary"] if r and not is_blank(r[0])}
        left = summary.get("Left over", [])
        return {
            "month": tab,
            "income": rows("income"), "rent": rent,
            "groups": {
                "Bills": rows("bills"),
                "Expenses": expenses,
                "Property": property_out,
                "Savings & invest": rows("savings"),
                "One-time": [{"name": "One-time (no fund)", "planned": 0.0, "actual": one_nf}],
            },
            "left_over": {"planned": parse_number(left[1]) if len(left) > 1 else 0.0,
                          "actual": parse_number(left[2]) if len(left) > 2 else 0.0},
        }
    return cache_get(f'insights_{tab}', 300, _fetch)


def get_fund_progress():
    def _fetch():
        try:
            rows_ = spreadsheet.values_batch_get(["Funds!A5:H14"], params={"valueRenderOption": "UNFORMATTED_VALUE"})
            vals = rows_.get("valueRanges", [{}])[0].get("values", [])
            return [{"name": str(r[0]).strip(), "target": parse_number(r[1]) if len(r) > 1 else 0.0,
                     "plan": parse_number(r[3]) if len(r) > 3 else 0.0,
                     "balance": parse_number(r[7]) if len(r) > 7 else 0.0}
                    for r in vals if r and not is_blank(r[0])]
        except Exception as e:
            logger.error(f"Error reading Funds: {e}")
            return []
    return cache_get('fund_progress', 300, _fetch)


@app.route("/insights")
def insights():
    months = list_month_tabs()
    tab = request.args.get("month") or (months[0] if months else None)
    data, error = None, None
    if tab not in months:
        error = "No month tabs on the new layout yet."
    else:
        try:
            data = get_insights(tab)
        except Exception as e:
            logger.error(f"Error building insights for {tab}: {e}")
            error = f"Couldn't read {tab}."
    return render_template("insights.html", months=months, month=tab, data=data,
                           funds=get_fund_progress(), error=error)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
