**What changes**
<!-- What the user of nimblegraph sees differently. Link the issue, if there is one. -->

**How it was checked**
<!-- The test you added, and the real project you ran it on for a language or framework change. -->

**Checklist**
- [ ] `python -m pytest -q` passes
- [ ] A test covers the behavior change
- [ ] JS/TS record changed: both engines (`node/extract.mjs` and `parse/`) changed together
- [ ] Per-file record changed: `EXTRACTOR_VERSION` bumped in `nimblegraph/build.py`
- [ ] UI changed: `npm run build` run and `nimblegraph/ui/dist/` committed
- [ ] README (en and pt-BR), ROADMAP and CHANGELOG updated when the change is visible to users
