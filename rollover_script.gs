/**
 * Budget Tools: monthly rollover for the Official_Budget sheet.
 *
 * Run it from the month tab you want to roll forward (e.g. Nov2026).
 * It copies that tab, names the copy for the next month (Dec2026), and
 * resets the cells you fill in by hand each month.
 *
 * Layout this script expects (Nov2026 redesign, Nov 2026):
 *   B2            Month text, e.g. "November 2026" (every formula keys off this)
 *   D11:D15       Income actuals (typed by hand)          -> cleared
 *   D48           Mortgage principal actually paid        -> cleared
 *   D55:D69       Accounts: Start of Month                -> set to last month's Actual End
 *   G55:G69       Accounts: Actual End                    -> cleared
 *   I55:I70       Account notes + net cash variance note  -> cleared
 * Budgets, bills and planned savings are kept so you only change what moved.
 *
 * Note: Apps Script is blocked on the work account. Open the sheet while
 * signed in to your personal Gmail to see the Budget Tools menu.
 */

// Cells that hold this month's typed-in actuals. Edit here if the layout changes.
var LAYOUT = {
  monthCell: "B2",
  clearRanges: ["D11:D15", "D48", "G55:G69", "I55:I70"],
  accountStart: "D55:D69",
  accountActualEnd: "G55:G69"
};

var MONTHS = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"];
var SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function rolloverMonth() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ui = SpreadsheetApp.getUi();
  var sheet = ss.getActiveSheet();

  // 1. Read and parse the month in B2 ("November 2026")
  var monthText = String(sheet.getRange(LAYOUT.monthCell).getDisplayValue()).trim();
  var parts = monthText.split(/\s+/);
  var monthIndex = MONTHS.indexOf(parts[0]);
  var year = parseInt(parts[1], 10);
  if (parts.length < 2 || monthIndex === -1 || isNaN(year)) {
    ui.alert("Run this from a month tab. B2 should look like 'November 2026' but it says: '" + monthText + "'.");
    return;
  }

  // 2. Work out next month's label and tab name
  var nextIndex = (monthIndex + 1) % 12;
  var nextYear = nextIndex === 0 ? year + 1 : year;
  var nextLabel = MONTHS[nextIndex] + " " + nextYear;   // "December 2026"
  var nextTab = SHORT[nextIndex] + nextYear;            // "Dec2026"

  if (ss.getSheetByName(nextTab)) {
    ui.alert("Tab '" + nextTab + "' already exists. Delete or rename it first if you want to roll over again.");
    return;
  }

  // 3. Warn if account balances for this month haven't been entered yet
  var actualEnds = sheet.getRange(LAYOUT.accountActualEnd).getValues();
  var anyEntered = actualEnds.some(function (r) { return r[0] !== ""; });
  var warning = anyEntered ? "" :
    "\n\nHeads up: no Actual End balances are filled in on this tab, so next month's Start of Month will be blank.";

  var answer = ui.alert(
    "Roll over to " + nextLabel + "?",
    "This will:\n" +
    "- Copy this tab as '" + nextTab + "' and put it first\n" +
    "- Set the month to " + nextLabel + "\n" +
    "- Carry this month's Actual End balances into Start of Month\n" +
    "- Clear income actuals, principal paid, Actual End and notes\n" +
    "- Keep all budgets and planned amounts" + warning,
    ui.ButtonSet.YES_NO
  );
  if (answer !== ui.Button.YES) return;

  // 4. Copy the tab (charts, formatting and formulas come with it)
  var newSheet = sheet.copyTo(ss).setName(nextTab);
  ss.setActiveSheet(newSheet);
  ss.moveActiveSheet(1);

  // 5. New month label. Every formula on the tab reads B2.
  newSheet.getRange(LAYOUT.monthCell).setValue(nextLabel);

  // 6. Last month's Actual End becomes this month's Start of Month (values only)
  newSheet.getRange(LAYOUT.accountStart).setValues(actualEnds);

  // 7. Clear the typed-in actuals for the new month
  LAYOUT.clearRanges.forEach(function (a1) {
    newSheet.getRange(a1).clearContent();
  });

  ui.alert("Done. '" + nextTab + "' is ready.\n\n" +
           "Expenses, savings, rent and property costs fill in automatically from the logs.\n" +
           "Type income actuals as paychecks land, and account balances at month end.");
}

// Adds the Budget Tools menu when the sheet opens
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Budget Tools")
    .addItem("Rollover to Next Month", "rolloverMonth")
    .addToUi();
}
