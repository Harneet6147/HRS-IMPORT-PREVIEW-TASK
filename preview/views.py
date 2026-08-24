"""The only view in the application: accept a CSV, show a preview.

The view is deliberately thin. Its job is to get bytes out of the request, hand
them to the pure functions in parsing.py and hierarchy.py, and pass the result to
a template. No analysis logic lives here, which is what keeps that logic testable
without a browser.

Nothing is written to a database at any point; the whole preview is computed in
memory from the uploaded bytes.
"""

from django.shortcuts import render

from .hierarchy import analyze_hierarchy
from .parsing import CsvFormatError, read_rows, validate_identities

# Roughly 100k employees at ~150 bytes per row is ~15 MB. This cap keeps a
# careless or hostile upload from exhausting memory, and gives a clear message
# instead of a server error.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def upload_preview(request):
    if request.method != "POST":
        return render(request, "preview/upload.html")

    uploaded = request.FILES.get("csv_file")

    if uploaded is None:
        return _error(request, "Please choose a CSV file to upload.")

    if uploaded.size == 0:
        return _error(request, "That file is empty.")

    if uploaded.size > MAX_UPLOAD_BYTES:
        return _error(
            request,
            f"That file is {uploaded.size / 1_048_576:.1f} MB, which is larger "
            f"than the {MAX_UPLOAD_BYTES // 1_048_576} MB limit.",
        )

    try:
        rows = read_rows(uploaded.read())
    except CsvFormatError as exc:
        # Expected, explainable failures: bad encoding, no header, missing columns.
        return _error(request, str(exc))
    except Exception:
        # Anything unexpected still reaches the user as a message rather than a
        # stack trace. In production this branch would also log the exception.
        return _error(
            request,
            "The file could not be processed. Please check that it is a valid "
            "UTF-8 CSV export and try again.",
        )

    accepted, identity_errors = validate_identities(rows)
    result = analyze_hierarchy(accepted)

    # Identity errors and manager errors are merged into one list so Client
    # Success sees a single ordered list of things to fix, keyed by row number.
    all_errors = sorted(
        identity_errors + result.errors,
        key=lambda e: (e.row_number, e.message),
    )

    return render(
        request,
        "preview/result.html",
        {
            "filename": uploaded.name,
            "total_rows": len(rows),
            "accepted_count": len(accepted),
            "errors": all_errors,
            "result": result,
        },
    )


def _error(request, message: str):
    return render(request, "preview/upload.html", {"error": message}, status=400)
