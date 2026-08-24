# HRIS Import Preview

A small Django app that takes a client's employee CSV and shows you what's wrong
with it before you import anything.

Client HR exports are messy. The same person shows up twice, someone's manager
isn't in the file, or two people list each other as their manager. This app reads
the file, tells you how many rows are usable, lists every problem with its row
number, and shows the reporting structure — including anyone stuck in a
reporting loop.

Nothing gets saved. It just reads the file and shows you the results.

## Setup

Needs Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py runserver
```

Go to http://127.0.0.1:8000/ and upload `sample_hris.csv` from this folder.

No migrations to run — there's no database at all.

## Tests

```bash
python manage.py test
```

37 tests. No browser or database needed.

## How it's organised

| File | What it does |
| --- | --- |
| `preview/parsing.py` | Reads the CSV, cleans up the values, checks each row has a valid ID and email |
| `preview/hierarchy.py` | Works out who reports to whom, counts direct reports, finds cycles |
| `preview/views.py` | Just the web bit — takes the upload, calls the other two, shows the page |
| `preview/templates/preview/` | Plain HTML |
| `preview/tests/` | Tests for each module, plus checks against the sample file |

`parsing.py` and `hierarchy.py` don't import Django at all. They take plain
Python in and give plain Python back, which is why the tests can call them
directly instead of going through a browser.

The data moves in one direction:

```
uploaded file
  -> read_rows()            cleaned-up rows
  -> validate_identities()  usable rows + list of bad ones
  -> analyze_hierarchy()    roots, manager counts, cycles, manager errors
  -> the page
```

## What the sample file gives you

| | |
| --- | --- |
| Rows in the file | 25 |
| Rows usable | 25 |
| Errors | 2 |
| People with no manager | 1 (DIV-1001) |
| People who manage someone | 13 |
| People stuck in a cycle | 3 (DIV-1701, DIV-1702, DIV-1703) |

The two errors: row 10 lists manager `DIV-9999`, who isn't in the file. Row 21
gives both a manager ID and a manager email, and they point at different people.

## Decisions I made

- **Row numbers are the actual line numbers in the file**, counting the header as
  line 1. So the first employee is row 2. That way the numbers match what you see
  if you open the file in Excel.
- **Only four columns are actually required**: `employee_id`, `email`,
  `manager_id`, `manager_email`. If `employee_name` or `department` are missing,
  the file still works — those are only displayed, they don't affect anything.
- **If an email or ID appears twice, both rows get rejected.** Not just the second
  one. I can't tell which row is the real person, so picking one would be a guess,
  and guessing wrong means attaching the org chart to the wrong human.
- **Blank isn't a duplicate.** Two rows with no employee ID each get a "this is
  required" error, not a "duplicate" error.
- **If someone's manager is on a row that got rejected, it shows as "not found".**
  Rejected rows are left out of the lookup, so the manager genuinely isn't there
  as far as the code is concerned.
- **UTF-8 only.** If the file is in another encoding, it says so instead of
  guessing — a wrong guess would quietly mangle people's names.

## Things I'd fix with more time

- **Only one manager error is shown per row.** If both the manager ID and the
  manager email are wrong, you only see the ID problem. The identity check
  already collects every problem on a row, so manager resolution should do the
  same. This is the first thing I'd change.
- **Cycles aren't grouped.** Two separate loops show up as one list of people.
  The code already knows which loop each person belongs to, so splitting them out
  wouldn't be much work — it just wasn't needed.
- **The whole file is loaded into memory**, capped at 25MB. Reading could be done
  row by row, but working out the hierarchy can't — a manager might be listed
  after their team, so you need everyone before you can start.
- **Repeated column names aren't caught.** If a file has `email` twice, Python's
  CSV reader keeps the last one and ignores the first.
- **No pagination.** A huge file with thousands of errors would be one very long
  page.
- **You can't download the error list.** In practice you'd want it as a CSV to
  send back to the client.

## Performance

Everything is one pass over the data, so it scales linearly with the number of
rows:

- **Reading the file** — one pass.
- **Checking IDs and emails** — counts everything first, then checks each row.
  Doing it the obvious way (comparing every row against every other row) would
  be far slower on a big file.
- **Finding managers** — builds two lookup tables, then each lookup is instant.
- **Finding cycles** — each person is visited once. It's written as a loop rather
  than a recursive function on purpose: Python can only go about 1,000 levels deep
  in recursion, and a 100,000-person file could easily have a longer reporting
  chain than that, which would crash it.

For 100,000 employees the limit is memory, not speed — you're holding the rows
and two lookup tables, which is tens of megabytes.

## Time spent

About 60 minutes, not counting the recording.

## AI tools used

I used Claude to set up the Django project and get a first version of the
parsing and hierarchy code and the tests.

The main thing I changed was cycle detection. The first version was recursive,
which reads more naturally in Python, but the brief mentions files up to 100,000
employees and Python only handles about 1,000 levels of recursion. A long
reporting chain would crash rather than give a wrong answer. Then I put the loop version back.

I also changed which columns are required. The first version demanded all six,
but `employee_name` and `department` are only displayed and don't affect any
rule, so rejecting a whole file because one of those was missing seemed wrong.
Now only the four columns that actually matter are required.
