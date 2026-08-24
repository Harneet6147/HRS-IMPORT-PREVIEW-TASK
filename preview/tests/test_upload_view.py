"""End-to-end tests: the HTTP layer, and the supplied sample file.

Uses Django's test client, which calls the view directly. Still no browser.
"""

from io import BytesIO

from django.conf import settings
from django.test import SimpleTestCase

from preview.hierarchy import analyze_hierarchy
from preview.parsing import read_rows, validate_identities

from .helpers import csv_bytes, row

SAMPLE = settings.BASE_DIR / "sample_hris.csv"


def upload(client, data: bytes, name: str = "hris.csv"):
    return client.post("/", {"csv_file": BytesIO(data)}, format="multipart", follow=False)


class UploadViewTests(SimpleTestCase):
    def test_get_shows_the_upload_form(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csv_file")

    def test_valid_upload_renders_the_preview(self):
        data = csv_bytes(
            row("DIV-1", "a@x.com"),
            row("DIV-2", "b@x.com", manager_id="DIV-1"),
        )

        response = upload(self.client, data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Import preview")
        self.assertEqual(response.context["total_rows"], 2)
        self.assertEqual(response.context["accepted_count"], 2)

    def test_garbage_upload_returns_a_message_not_a_stack_trace(self):
        response = upload(self.client, b"\xff\xfe\x00\x01\x02 definitely not csv")

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Could not read that file", status_code=400)

    def test_upload_with_no_file_selected_is_handled(self):
        response = self.client.post("/", {})

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "choose a CSV file", status_code=400)


class SampleFileTests(SimpleTestCase):
    """Locks in the expected preview for the file supplied with the exercise.

    If any rule changes behaviour by accident, these numbers move and the test
    fails, which is cheaper than re-checking the output by eye.
    """

    def setUp(self):
        rows = read_rows(SAMPLE.read_bytes())
        self.rows = rows
        self.accepted, self.identity_errors = validate_identities(rows)
        self.result = analyze_hierarchy(self.accepted)

    def test_row_and_acceptance_counts(self):
        self.assertEqual(len(self.rows), 25)
        self.assertEqual(len(self.accepted), 25)
        self.assertEqual(self.identity_errors, [])

    def test_single_root_is_the_ceo(self):
        self.assertEqual([e.employee_id for e in self.result.roots], ["DIV-1001"])

    def test_two_manager_errors_are_reported(self):
        """DIV-1600 points at a non-existent id; DIV-1601's two references conflict."""
        by_employee = {e.employee_id: e.message for e in self.result.errors}

        self.assertEqual(set(by_employee), {"DIV-1600", "DIV-1601"})
        self.assertIn("does not match", by_employee["DIV-1600"])
        self.assertIn("same employee", by_employee["DIV-1601"])

    def test_research_department_forms_a_three_person_cycle(self):
        flagged = {employee.employee_id for employee, _ in self.result.cycle_members}

        self.assertEqual(flagged, {"DIV-1701", "DIV-1702", "DIV-1703"})

    def test_quoted_name_with_a_comma_survived_parsing(self):
        renee = next(e for e in self.accepted if e.employee_id == "DIV-1412")

        self.assertEqual(renee.employee_name, "Alvarez, Renée")
