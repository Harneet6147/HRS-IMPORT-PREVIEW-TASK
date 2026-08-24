"""Tests for reading the file and deciding which rows have a usable identity.

These call parsing.py directly. No Django test client, no browser.
"""

from django.test import SimpleTestCase

from preview.parsing import CsvFormatError, read_rows, validate_identities

from .helpers import HEADER, csv_bytes, row


class ReadRowsTests(SimpleTestCase):
    def test_quoted_comma_in_name_is_one_field(self):
        """A name containing a comma must not split into two columns.

        This is the reason for using the csv module instead of str.split(",").
        """
        rows = read_rows(
            csv_bytes('DIV-1,"Alvarez, Renee",a@x.com,,,Operations')
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].employee_name, "Alvarez, Renee")
        self.assertEqual(rows[0].department, "Operations")

    def test_bom_and_whitespace_and_case_are_normalized(self):
        """UTF-8 BOM, padding spaces, and email casing all normalize away.

        Without BOM handling the first header would be read as
        '\ufeffemployee_id' and every employee_id would come back blank.
        """
        rows = read_rows(
            csv_bytes("  DIV-1 , Ada ,  ADA@X.COM ,,  BOSS@X.COM  , Eng ", bom=True)
        )

        self.assertEqual(rows[0].employee_id, "DIV-1")
        self.assertEqual(rows[0].email, "ada@x.com")
        self.assertEqual(rows[0].manager_email, "boss@x.com")

    def test_employee_id_stays_case_sensitive(self):
        """IDs are only trimmed, never lowercased, per the CSV contract."""
        rows = read_rows(csv_bytes(row("DIV-Abc", "a@x.com")))

        self.assertEqual(rows[0].employee_id, "DIV-Abc")

    def test_headers_may_appear_in_any_order(self):
        reordered = "department,email,manager_email,employee_name,manager_id,employee_id"
        rows = read_rows(csv_bytes("Eng,a@x.com,,Ada,DIV-9,DIV-1", header=reordered))

        self.assertEqual(rows[0].employee_id, "DIV-1")
        self.assertEqual(rows[0].manager_id, "DIV-9")

    def test_row_numbers_match_lines_in_the_file(self):
        """The header is line 1, so the first data row reports as line 2."""
        rows = read_rows(csv_bytes(row("DIV-1", "a@x.com"), row("DIV-2", "b@x.com")))

        self.assertEqual([r.row_number for r in rows], [2, 3])

    def test_missing_required_column_raises_a_clear_error(self):
        header_without_email = "employee_id,employee_name,manager_id,manager_email,department"

        with self.assertRaises(CsvFormatError) as ctx:
            read_rows(csv_bytes("DIV-1,Ada,,,Eng", header=header_without_email))

        self.assertIn("email", str(ctx.exception))

    def test_non_utf8_bytes_raise_a_clear_error_not_a_crash(self):
        """A malformed upload must produce a message, not an unhandled exception."""
        with self.assertRaises(CsvFormatError):
            read_rows(b"\xff\xfe\x00\x01 not text at all")

    def test_empty_file_raises_a_clear_error(self):
        with self.assertRaises(CsvFormatError):
            read_rows(b"")

    def test_optional_columns_may_be_absent(self):
        """department and employee_name are cosmetic, so their absence is tolerated."""
        header = "employee_id,email,manager_id,manager_email"
        rows = read_rows(csv_bytes("DIV-1,a@x.com,,", header=header))

        self.assertEqual(rows[0].employee_id, "DIV-1")
        self.assertEqual(rows[0].department, "")


class ValidateIdentitiesTests(SimpleTestCase):
    def test_duplicate_email_invalidates_every_row_that_shares_it(self):
        """Both rows are rejected, not just the second one.

        A duplicated email makes both rows ambiguous: we cannot tell which one
        is the real person, so neither may take part in hierarchy analysis.
        """
        rows = read_rows(
            csv_bytes(
                row("DIV-1", "shared@x.com"),
                row("DIV-2", "SHARED@x.com"),   # same address after lowercasing
                row("DIV-3", "unique@x.com"),
            )
        )

        accepted, errors = validate_identities(rows)

        self.assertEqual([e.employee_id for e in accepted], ["DIV-3"])
        self.assertEqual(sorted(e.row_number for e in errors), [2, 3])

    def test_duplicate_employee_id_invalidates_every_row_that_shares_it(self):
        rows = read_rows(
            csv_bytes(row("DIV-1", "a@x.com"), row("DIV-1", "b@x.com"))
        )

        accepted, errors = validate_identities(rows)

        self.assertEqual(accepted, [])
        self.assertEqual(len(errors), 2)

    def test_blank_required_fields_are_rejected_with_row_numbers(self):
        rows = read_rows(
            csv_bytes(row("", "a@x.com"), row("DIV-2", ""), row("DIV-3", "c@x.com"))
        )

        accepted, errors = validate_identities(rows)

        self.assertEqual([e.employee_id for e in accepted], ["DIV-3"])
        self.assertEqual({e.row_number for e in errors}, {2, 3})
