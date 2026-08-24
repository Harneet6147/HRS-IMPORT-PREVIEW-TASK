"""Small helpers so each test can state only the rows it cares about."""

HEADER = "employee_id,employee_name,email,manager_id,manager_email,department"


def csv_bytes(*rows: str, header: str = HEADER, bom: bool = False) -> bytes:
    """Build an in-memory CSV file from row strings."""
    text = "\n".join((header, *rows)) + "\n"
    return ("\ufeff" + text if bom else text).encode("utf-8")


def row(
    employee_id: str,
    email: str,
    manager_id: str = "",
    manager_email: str = "",
    name: str = "",
    department: str = "",
) -> str:
    name = name or employee_id
    return f"{employee_id},{name},{email},{manager_id},{manager_email},{department}"
