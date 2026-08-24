"""Manager resolution and reporting-hierarchy analysis.

Like parsing.py, this module is framework-free so it can be tested directly.

    list[SourceRow] -> analyze_hierarchy() -> HierarchyResult

Key structural fact used throughout: every employee has *at most one* manager.
The reporting graph is therefore a "functional graph" -- each node has out-degree
0 or 1. That lets cycle detection be a simple forward walk instead of a general
recursive depth-first search, which matters for large files (see find_cycle_members).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .parsing import RowError, SourceRow


@dataclass
class HierarchyResult:
    """Everything the template needs to render the hierarchy section."""

    # employee_id -> manager's employee_id, for confirmed relationships only
    manager_of: dict[str, str] = field(default_factory=dict)
    # manager's employee_id -> list of direct reports' employee_ids
    reports_of: dict[str, list[str]] = field(default_factory=dict)
    # employees with both manager fields blank and no manager error
    roots: list[SourceRow] = field(default_factory=list)
    # (manager, direct report count), highest count first
    managers: list[tuple[SourceRow, int]] = field(default_factory=list)
    # (employee, that employee's manager) for actual members of a reporting
    # cycle. Carrying the manager lets the UI show the loop itself, not just a
    # list of names.
    cycle_members: list[tuple[SourceRow, SourceRow]] = field(default_factory=list)
    # manager-resolution problems, one per affected row
    errors: list[RowError] = field(default_factory=list)


def resolve_managers(
    accepted: list[SourceRow],
) -> tuple[dict[str, str], list[SourceRow], list[RowError]]:
    """Turn the manager_id / manager_email columns into concrete relationships.

    Returns (manager_of, roots, errors).

    Two lookup dictionaries are built first, so resolving each employee's
    manager is an O(1) hash lookup rather than a scan of the whole file. Because
    both dictionaries are fully built before any resolution happens, a manager
    may appear anywhere in the file -- before or after their reports.

    Only accepted employees go into the lookups. That is what enforces the rule
    that identity-invalid rows do not participate in manager lookup: pointing at
    a duplicated or incomplete row reads as "manager not found".

    An employee with a manager error stays accepted, but produces no
    relationship and is not counted as a root.
    """
    by_id = {e.employee_id: e for e in accepted}
    by_email = {e.email: e for e in accepted}

    manager_of: dict[str, str] = {}
    roots: list[SourceRow] = []
    errors: list[RowError] = []

    for employee in accepted:
        has_id_ref = bool(employee.manager_id)
        has_email_ref = bool(employee.manager_email)

        # Rule 1: both manager fields blank -> this person is a root.
        if not has_id_ref and not has_email_ref:
            roots.append(employee)
            continue

        from_id = by_id.get(employee.manager_id) if has_id_ref else None
        from_email = by_email.get(employee.manager_email) if has_email_ref else None

        problem: str | None = None

        if has_id_ref and from_id is None:
            problem = (
                f"manager_id '{employee.manager_id}' does not match any "
                f"accepted employee in this file"
            )
        elif has_email_ref and from_email is None:
            problem = (
                f"manager_email '{employee.manager_email}' does not match any "
                f"accepted employee in this file"
            )
        elif (
            has_id_ref
            and has_email_ref
            and from_id.employee_id != from_email.employee_id
        ):
            # Rule 4: if both references are supplied they must agree.
            problem = (
                f"manager_id '{employee.manager_id}' points to "
                f"{from_id.employee_id} but manager_email "
                f"'{employee.manager_email}' points to {from_email.employee_id}; "
                f"the two references must identify the same employee"
            )

        if problem is None:
            manager = from_id or from_email
            if manager.employee_id == employee.employee_id:
                problem = "employee is listed as their own manager"

        if problem is not None:
            errors.append(
                RowError(
                    row_number=employee.row_number,
                    employee_id=employee.employee_id,
                    message=problem,
                )
            )
            continue

        manager_of[employee.employee_id] = (from_id or from_email).employee_id

    return manager_of, roots, errors


def find_cycle_members(manager_of: dict[str, str]) -> set[str]:
    """Return the ids of employees that are genuinely inside a reporting cycle.

    Because each employee has at most one manager, following manager_of from any
    starting employee gives a single chain. That chain either runs out (someone
    has no manager) or eventually revisits a node, which means a cycle.

    The walk colours each node:
        absent    -> not seen yet
        IN_PATH   -> on the chain currently being walked
        SETTLED   -> fully processed by an earlier walk

    We stop a walk as soon as we reach a node that is not "not seen yet". If that
    node is IN_PATH, it belongs to the chain we are on right now, so everything
    from that node forward is the cycle. Anything walked *before* that point led
    into the cycle without being part of it, and is deliberately excluded --
    that is the "reports into a cycle" case the brief calls out.

    If the node is SETTLED, an earlier walk already classified it and everything
    above it, so we stop without re-deciding anything. Each node is therefore
    visited a constant number of times overall: O(n) time, O(n) space.

    Written as a loop rather than recursion on purpose. A 100,000-employee file
    could contain a 100,000-deep chain, which would exceed Python's recursion
    limit and crash a recursive implementation.
    """
    IN_PATH, SETTLED = 1, 2
    state: dict[str, int] = {}
    cycle_members: set[str] = set()

    for start in manager_of:
        if start in state:
            continue

        path: list[str] = []
        position_in_path: dict[str, int] = {}
        node: str | None = start

        while node is not None and node not in state:
            state[node] = IN_PATH
            position_in_path[node] = len(path)
            path.append(node)
            node = manager_of.get(node)

        # We stopped. If we stopped on a node still marked IN_PATH, we closed a
        # loop within the current chain.
        if node is not None and state.get(node) == IN_PATH:
            cycle_members.update(path[position_in_path[node] :])

        for visited in path:
            state[visited] = SETTLED

    return cycle_members


def analyze_hierarchy(accepted: list[SourceRow]) -> HierarchyResult:
    """Run the full hierarchy analysis over the accepted employees."""
    manager_of, roots, errors = resolve_managers(accepted)

    reports_of: dict[str, list[str]] = {}
    for employee_id, manager_id in manager_of.items():
        reports_of.setdefault(manager_id, []).append(employee_id)

    by_id = {e.employee_id: e for e in accepted}

    # Only people who actually have direct reports are listed as managers.
    managers = sorted(
        ((by_id[manager_id], len(reports)) for manager_id, reports in reports_of.items()),
        key=lambda pair: (-pair[1], pair[0].employee_id),
    )

    cycle_ids = find_cycle_members(manager_of)
    cycle_members = sorted(
        (
            (by_id[employee_id], by_id[manager_of[employee_id]])
            for employee_id in cycle_ids
        ),
        key=lambda pair: pair[0].row_number,
    )

    return HierarchyResult(
        manager_of=manager_of,
        reports_of=reports_of,
        roots=sorted(roots, key=lambda e: e.row_number),
        managers=managers,
        cycle_members=cycle_members,
        errors=errors,
    )
