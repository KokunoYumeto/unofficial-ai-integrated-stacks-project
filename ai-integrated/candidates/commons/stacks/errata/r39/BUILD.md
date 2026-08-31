# R39 build recipe — not yet executed

After source preparation, use replay-build.py with a pinned upstream source directory, a new absent work directory, and a private evidence directory. It builds candidate and authority sequentially. Repeat with a second new work directory and separate private evidence; then run deterministic-replay.py --first-private-build-root with the first evidence directory and build-receipt.py deliberately.

Use derive-visual-pages.py on a source/PDF-matched SyncTeX build. Render every actual PDF page using render-qa.py and the actual sensitive page inventory. Actual page inspection and a new independent candidate replay must precede visual PASS or final manifest closure. No inherited R38 page counts, layouts, or passing receipts apply.

Source date epoch is fixed in candidate.config.json. Build helpers are prepared only; this source worker does not invoke TeX.
