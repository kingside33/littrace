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
