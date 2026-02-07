function rolloverMonth() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ui = SpreadsheetApp.getUi();
  var sheet = ss.getActiveSheet();

  // Get current month/year from B2
  var monthYearText = sheet.getRange("B2").getValue();

  var months = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];
  var shortMonths = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // Parse current month and year from B2
  var parts = monthYearText.toString().trim().split(" ");
  if (parts.length < 2) {
    ui.alert("Error: B2 should be in format 'January 2026'. Found: " + monthYearText);
    return;
  }

  var monthName = parts[0];
  var year = parseInt(parts[1]);
  var currentMonthIndex = months.indexOf(monthName);

  if (currentMonthIndex === -1) {
    ui.alert("Error: Could not parse month from B2: " + monthName);
    return;
  }

  // Calculate next month
  var nextMonthIndex = (currentMonthIndex + 1) % 12;
  var nextYear = nextMonthIndex === 0 ? year + 1 : year;
  var nextMonthFull = months[nextMonthIndex] + " " + nextYear;
  var nextTabName = shortMonths[nextMonthIndex] + nextYear;

  // Check if next month tab already exists
  if (ss.getSheetByName(nextTabName)) {
    ui.alert("Tab '" + nextTabName + "' already exists! Delete it first if you want to re-roll.");
    return;
  }

  // Confirm with user
  var response = ui.alert(
    "Rollover to " + nextMonthFull + "?",
    "This will:\n" +
    "• Duplicate this tab as '" + nextTabName + "'\n" +
    "• Update the month to " + nextMonthFull + "\n" +
    "• Reset manual actuals (Misc Income, Plug Number) to 0\n" +
    "• Expense + savings formulas will auto-update for the new month",
    ui.ButtonSet.YES_NO
  );

  if (response !== ui.Button.YES) return;

  // Duplicate the current sheet
  var newSheet = sheet.copyTo(ss);
  newSheet.setName(nextTabName);

  // Move new sheet right after the current one
  var currentIndex = sheet.getIndex();
  ss.setActiveSheet(newSheet);
  ss.moveActiveSheet(currentIndex + 1);

  // Update B2 to the new month
  newSheet.getRange("B2").setValue(nextMonthFull);

  // Reset hardcoded actual values to 0 for the new month
  // D7  = Miscellaneous income actual
  // D37 = Plug Number actual
  // NOTE: D30-D32 (Roth IRA, Investments, Savings) are now formula-driven
  //       via SUMPRODUCT pulling from Expense Responses, so they auto-update
  //       when B2 changes to the new month — do NOT reset them.
  var cellsToReset = ["D7", "D37"];
  cellsToReset.forEach(function(cellRef) {
    newSheet.getRange(cellRef).setValue(0);
  });

  // Switch to the new sheet
  ss.setActiveSheet(newSheet);

  ui.alert("Done! '" + nextTabName + "' has been created.\n\n" +
           "Expense + savings actuals will auto-populate from Expense Responses for " + nextMonthFull + ".\n" +
           "Fill in miscellaneous income actuals as the month progresses.");
}

// Adds a custom menu to the spreadsheet
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Budget Tools")
    .addItem("Rollover to Next Month", "rolloverMonth")
    .addToUi();
}
