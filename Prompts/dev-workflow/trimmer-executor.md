Execute only the findings I approve by ID: <T-IDs>.
Work on a new branch named trim/<date>.
Before starting: run the full test suite and record the baseline. If it is
not green, stop and report.
For each approved finding:

1. Make the single change described. Remove the tests the finding lists
   (ORPHAN tests tied to it) in the same change. Nothing else.
2. Run the tests.
3. Green: commit with "trim: T-00X <summary>".
   Red: look at each failing test. - If it is listed as an ORPHAN test for this finding, delete it and
   re-run. - If it is not listed, or it checks behavior from an acceptance
   criterion, do NOT delete it. Revert the change, mark the finding
   BLOCKED with the failing test names, and continue to the next.
   Never edit or delete a test just to make a removal pass.
   Approved TT-IDs (test rewrites or drops) are done as their own commits.
   Do not fix, refactor, or improve anything not in an approved finding.
   If you notice something else, add it to a "Noticed" list. Do not act on it.
   Finish with: findings applied, findings blocked, lines removed, final test
   result, Noticed list.
