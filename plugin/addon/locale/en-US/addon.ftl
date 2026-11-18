# Fluent strings for littrace. The build renames this file to
# littrace-addon.ftl and prefixes every key with "littrace-"; look keys up
# without the prefix from plugin code.
startup-begin = littrace is loading
startup-finish = littrace is ready

# Reader evidence-Q&A panel (A3 prototype). All content is static fake data.
reader-panel-title = Evidence Q&A
reader-panel-demo-note = Prototype: static fake data, no local service
reader-panel-placeholder = Ask about this paper
reader-panel-ask = Ask
reader-panel-example = For example: under which experimental conditions does this method hold?
reader-panel-wait = The previous question is still running; submit again when it finishes
reader-panel-empty = No question yet. Type one, or select text and use Check evidence.
reader-panel-stage-searching = Retrieving evidence
reader-panel-stage-verifying = Verifying claims
reader-panel-gap = Gap
reader-panel-gap-hint = No original evidence with a locatable page answered this question; only the question is kept.
reader-panel-evidence = Evidence text
reader-panel-failure = This round did not finish
reader-panel-retry = Retry
reader-panel-toolbar-entry = Evidence Q&A
reader-panel-close = Close evidence Q&A
reader-selection-evidence = Check evidence
reader-selection-lineage = Ask lineage

# Titles for the four iframe function tabs (U4). The tab registry references
# these keys; the titles themselves are maintained only here.
tab-title-graph = Research Lineage
tab-title-search = Evidence Search
tab-title-research = Research Dashboard
tab-title-approvals = Full-text Approvals

# Item-pane sections (U2): evidence / lineage / close reading.
# l10nIDs and t() both carry the build's littrace- prefix; the keys here are bare.
section-evidence = Evidence
section-lineage = Lineage
section-reading = Close reading
section-evidence-tooltip = Evidence: indexed passages, figures and formulas
section-lineage-tooltip = Lineage: confirmed method relations
section-reading-tooltip = Close reading: coverage and page classes
section-unindexed = This paper has no searchable evidence yet
section-open-search = Search
section-no-relations = No relations yet
section-open-lineage = Open lineage
section-unparsed = Not parsed yet
section-error = Could not load
section-retry = Retry
section-offline = Local service required
section-offline-guide = The littrace local service is not running. Start it, then press Retry to reload; this panel never shows half-loaded data.
section-parse = Parse this paper
section-parse-submitting = Submitting…
section-parse-queued = Added to the parse queue
section-parse-unknown = The service did not confirm a task; check the task list shortly
section-direction-previous = Earlier work
section-direction-following = Later work
section-direction-unknown = Year unverified
section-lineage-pending = Pending
section-lineage-citations = Citations
section-reading-state-unparsed = Not parsed
section-reading-state-partial = Partially parsed
section-reading-state-complete = Parsing complete
section-reading-state-stale = Inputs changed, re-read needed
evidence-kind-paper_text = Passages
evidence-kind-figure = Figures
evidence-kind-formula = Formulas
evidence-kind-metadata = Metadata
evidence-kind-external_abstract = External abstracts
reading-class-NATIVE_TEXT = Native text
reading-class-SCANNED = Scanned
reading-class-MIXED = Mixed
reading-class-LOW_QUALITY = Low quality
reading-class-FAILED = Parse failed
