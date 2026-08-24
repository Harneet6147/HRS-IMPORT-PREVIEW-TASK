"""Tests for manager resolution and reporting-cycle detection."""

from django.test import SimpleTestCase

from preview.hierarchy import analyze_hierarchy, find_cycle_members
from preview.parsing import read_rows, validate_identities

from .helpers import csv_bytes, row


def analyze(*rows: str):
    """Run the full pipeline over some rows and return the hierarchy result."""
    accepted, _ = validate_identities(read_rows(csv_bytes(*rows)))
    return accepted, analyze_hierarchy(accepted)


class ManagerResolutionTests(SimpleTestCase):
    def test_blank_manager_fields_make_a_root(self):
        _, result = analyze(row("DIV-1", "a@x.com"))

        self.assertEqual([e.employee_id for e in result.roots], ["DIV-1"])

    def test_manager_may_be_defined_after_their_report(self):
        """Lookups are built from the whole file first, so order does not matter."""
        _, result = analyze(
            row("DIV-2", "b@x.com", manager_id="DIV-1"),
            row("DIV-1", "a@x.com"),
        )

        self.assertEqual(result.manager_of, {"DIV-2": "DIV-1"})
        self.assertEqual(result.errors, [])

    def test_manager_email_lookup_ignores_case(self):
        _, result = analyze(
            row("DIV-1", "boss@x.com"),
            row("DIV-2", "b@x.com", manager_email="BOSS@X.COM"),
        )

        self.assertEqual(result.manager_of, {"DIV-2": "DIV-1"})

    def test_conflicting_manager_id_and_email_is_an_error(self):
        """Both references were supplied but point at different people.

        This is the DIV-1601 case in the sample file. We refuse to guess which
        reference is correct; a silent pick would corrupt the client's org chart.
        """
        _, result = analyze(
            row("DIV-1", "a@x.com"),
            row("DIV-2", "b@x.com"),
            row("DIV-3", "c@x.com", manager_id="DIV-1", manager_email="b@x.com"),
        )

        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].employee_id, "DIV-3")
        self.assertIn("same employee", result.errors[0].message)
        self.assertNotIn("DIV-3", result.manager_of)

    def test_agreeing_manager_id_and_email_resolve_normally(self):
        _, result = analyze(
            row("DIV-1", "a@x.com"),
            row("DIV-2", "b@x.com", manager_id="DIV-1", manager_email="a@x.com"),
        )

        self.assertEqual(result.errors, [])
        self.assertEqual(result.manager_of, {"DIV-2": "DIV-1"})

    def test_unknown_manager_reference_is_an_error_with_a_row_number(self):
        _, result = analyze(
            row("DIV-1", "a@x.com"),
            row("DIV-2", "b@x.com", manager_id="DIV-NOPE"),
        )

        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].row_number, 3)

    def test_self_manager_is_an_error(self):
        _, result = analyze(row("DIV-1", "a@x.com", manager_id="DIV-1"))

        self.assertEqual(len(result.errors), 1)
        self.assertIn("own manager", result.errors[0].message)

    def test_employee_with_a_manager_error_is_still_accepted_but_is_not_a_root(self):
        """The rule that is easiest to get wrong.

        A bad manager reference is a relationship problem, not an identity
        problem. The person exists, so they stay in the accepted count -- but
        they must not be reported as a root, because we do not know that they
        have no manager. We only know we could not resolve the one they claim.
        """
        accepted, result = analyze(
            row("DIV-1", "a@x.com"),
            row("DIV-2", "b@x.com", manager_id="DIV-NOPE"),
        )

        self.assertEqual(len(accepted), 2)
        self.assertEqual([e.employee_id for e in result.roots], ["DIV-1"])
        self.assertNotIn("DIV-2", result.manager_of)

    def test_manager_pointing_at_an_identity_invalid_row_is_not_found(self):
        """Invalid rows are excluded from lookups, so they cannot be managers."""
        _, result = analyze(
            row("DIV-1", "dup@x.com"),
            row("DIV-9", "dup@x.com"),          # both rows above are invalid
            row("DIV-2", "b@x.com", manager_id="DIV-1"),
        )

        self.assertEqual(len(result.errors), 1)
        self.assertIn("does not match", result.errors[0].message)

    def test_direct_report_counts(self):
        _, result = analyze(
            row("DIV-1", "a@x.com"),
            row("DIV-2", "b@x.com", manager_id="DIV-1"),
            row("DIV-3", "c@x.com", manager_id="DIV-1"),
            row("DIV-4", "d@x.com", manager_id="DIV-2"),
        )

        counts = {manager.employee_id: n for manager, n in result.managers}
        self.assertEqual(counts, {"DIV-1": 2, "DIV-2": 1})


class CycleDetectionTests(SimpleTestCase):
    def test_two_person_cycle_is_detected(self):
        members = find_cycle_members({"A": "B", "B": "A"})

        self.assertEqual(members, {"A", "B"})

    def test_someone_reporting_into_a_cycle_is_not_flagged(self):
        """The distinction the brief calls out explicitly.

        C reports to A, and A and B report to each other. C can never reach a
        root, but C is not a member of the loop: removing C leaves the cycle
        intact. Only A and B may be flagged.
        """
        members = find_cycle_members({"A": "B", "B": "A", "C": "A", "D": "C"})

        self.assertEqual(members, {"A", "B"})

    def test_a_clean_chain_has_no_cycle(self):
        members = find_cycle_members({"C": "B", "B": "A"})

        self.assertEqual(members, set())

    def test_two_separate_cycles_are_both_detected(self):
        members = find_cycle_members({"A": "B", "B": "A", "X": "Y", "Y": "Z", "Z": "X"})

        self.assertEqual(members, {"A", "B", "X", "Y", "Z"})

    def test_long_chain_does_not_hit_the_recursion_limit(self):
        """Guards the decision to write the walk iteratively rather than recursively.

        A recursive depth-first search would raise RecursionError here, which is
        realistic for a 100,000-employee file with a deep chain.
        """
        manager_of = {f"E{i}": f"E{i + 1}" for i in range(20_000)}
        manager_of["E20000"] = "E0"  # close the loop

        members = find_cycle_members(manager_of)

        self.assertEqual(len(members), 20_001)

    def test_cycle_in_the_full_pipeline_reports_each_member_with_their_manager(self):
        _, result = analyze(
            row("DIV-1", "a@x.com", manager_id="DIV-2"),
            row("DIV-2", "b@x.com", manager_id="DIV-1"),
            row("DIV-3", "c@x.com", manager_id="DIV-1"),  # reports into the cycle
        )

        flagged = {employee.employee_id for employee, _ in result.cycle_members}
        self.assertEqual(flagged, {"DIV-1", "DIV-2"})
        self.assertEqual(result.roots, [])
