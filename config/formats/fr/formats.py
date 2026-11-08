# The French interface keeps English number/date formats on purpose
# (project rule: 1,500 · Dec 27, 2026 · 8:00 PM in both languages).
# Month names in public pages are rendered through the `endate` filter,
# which forces English.
DATE_FORMAT = "M j, Y"
TIME_FORMAT = "g:i A"
DATETIME_FORMAT = "M j, Y · g:i A"
SHORT_DATE_FORMAT = "m/d/Y"
SHORT_DATETIME_FORMAT = "m/d/Y g:i A"
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]
DATETIME_INPUT_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"]
DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = ","
NUMBER_GROUPING = 3
