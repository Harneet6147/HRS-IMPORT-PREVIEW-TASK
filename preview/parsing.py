"""Reading and validating the uploaded HRIS CSV.

This module has no Django imports on purpose. Everything here takes bytes or
plain Python objects and returns plain Python objects, so the whole parsing and
validation layer can be tested without starting a web server or a browser.

Pipeline exposed by this module:

    bytes -> read_rows() -> list[SourceRow]
    list[SourceRow] -> validate_identities() -> (accepted employees, row errors)
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass

# Columns the analysis actually depends on. If one of these is missing we
# cannot produce a meaningful preview, so we reject the file outright.
REQUIRED_HEADERS = ("employee_id", "email", "manager_id", "manager_email")

# Columns we display but can live without. Missing ones are treated as blank.
OPTIONAL_HEADERS = ("employee_name", "department")


class CsvFormatError(Exception):
    """The upload could not be interpreted as an HRIS CSV at all.

    This is for whole-file problems (undecodable bytes, no header row, missing
    required columns). Problems with individual rows are reported as RowError
    instead, so that one bad row never sinks the entire preview.
    """


@dataclass(frozen=True)
class SourceRow:
    """One normalized data row from the file.

    row_number is the line number in the original file, where the header is
    line 1. That is what the user sees when they open the file in a spreadsheet,
    which makes the error list actionable.
    """

    row_number: int
    employee_id: str
    employee_name: str
    email: str
    manager_id: str
    manager_email: str
    department: str


@dataclass(frozen=True)
class RowError:
    """A problem attached to a specific source row."""

    row_number: int
    employee_id: str
    message: str


def _clean(value: str | None) -> str:
    """Trim a raw CSV value. Missing columns arrive as None from DictReader."""
    return (value or "").strip()


def decode_upload(raw: bytes) -> str:
    """Decode the uploaded bytes as UTF-8, with or without a byte-order mark.

    'utf-8-sig' strips a leading BOM if present and behaves exactly like plain
    'utf-8' if it is not, so one codec covers both cases in the CSV contract.
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvFormatError(
            "The file could not be read as UTF-8 text. Please re-export it as "
            "UTF-8 encoded CSV and upload it again."
        ) from exc


def read_rows(raw: bytes) -> list[SourceRow]:
    """Parse uploaded bytes into normalized SourceRow objects.

    Uses the standard library csv module rather than splitting on commas, so
    quoted values containing commas (e.g. "Alvarez, Renee") parse correctly.

    Raises CsvFormatError for problems with the file as a whole.
    """
    text = decode_upload(raw)

    # newline="" is the documented way to hand text to the csv module: it lets
    # csv handle newlines itself, including newlines inside quoted fields.
    reader = csv.DictReader(io.StringIO(text, newline=""))

    if not reader.fieldnames:
        raise CsvFormatError("The file is empty or has no header row.")

    # Headers may appear in any order, so we always read by name. We also
    # normalize the header names themselves, which makes the parser tolerant of
    # exports with stray spaces or inconsistent casing in the header line.
    reader.fieldnames = [_clean(name).lower() for name in reader.fieldnames]

    missing = [h for h in REQUIRED_HEADERS if h not in reader.fieldnames]
    if missing:
        raise CsvFormatError(
            "The file is missing required column(s): " + ", ".join(missing) + ". "
            "Expected columns: "
            + ", ".join(REQUIRED_HEADERS + OPTIONAL_HEADERS)
            + "."
        )

    rows: list[SourceRow] = []
    try:
        for record in reader:
            rows.append(
                SourceRow(
                    # line_num is the line in the source file, so the header is
                    # 1 and the first data row is 2. It stays correct even when
                    # a quoted field contains a newline.
                    row_number=reader.line_num,
                    employee_id=_clean(record.get("employee_id")),
                    employee_name=_clean(record.get("employee_name")),
                    # Emails are lowercased so that DEMO.SOFIA.CHEN@... and
                    # demo.sofia.chen@... are the same person. Employee IDs are
                    # left case-sensitive, as the contract requires.
                    email=_clean(record.get("email")).lower(),
                    manager_id=_clean(record.get("manager_id")),
                    manager_email=_clean(record.get("manager_email")).lower(),
                    department=_clean(record.get("department")),
                )
            )
    except csv.Error as exc:
        raise CsvFormatError(f"The file is not valid CSV: {exc}") from exc

    return rows


def validate_identities(rows: list[SourceRow]) -> tuple[list[SourceRow], list[RowError]]:
    """Split rows into those accepted for analysis and the errors found.

    Identity rules:
      * employee_id and email are both required.
      * Both must be unique across the file after normalization.
      * Every row sharing a duplicated id or email is invalid, not just the
        second one. A duplicate makes both rows ambiguous, so neither can be
        trusted to represent a real person.

    Invalid rows are excluded from the returned list, which is what keeps them
    out of manager lookup and hierarchy analysis later.

    Two passes: one to count, one to judge. This is O(n) rather than the O(n^2)
    of re-scanning the list for each row.
    """
    id_counts = Counter(r.employee_id for r in rows if r.employee_id)
    email_counts = Counter(r.email for r in rows if r.email)

    accepted: list[SourceRow] = []
    errors: list[RowError] = []

    for row in rows:
        problems: list[str] = []

        if not row.employee_id:
            problems.append("employee_id is required but is blank")
        elif id_counts[row.employee_id] > 1:
            problems.append(
                f"employee_id '{row.employee_id}' appears on "
                f"{id_counts[row.employee_id]} rows and must be unique"
            )

        if not row.email:
            problems.append("email is required but is blank")
        elif email_counts[row.email] > 1:
            problems.append(
                f"email '{row.email}' appears on "
                f"{email_counts[row.email]} rows and must be unique"
            )

        if problems:
            for message in problems:
                errors.append(
                    RowError(
                        row_number=row.row_number,
                        employee_id=row.employee_id,
                        message=message,
                    )
                )
        else:
            accepted.append(row)

    return accepted, errors
