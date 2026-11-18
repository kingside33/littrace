# Fluent strings for littrace. The build renames this file to
# littrace-addon.ftl and prefixes every key with "littrace-"; look keys up
# without the prefix from plugin code.
startup-begin = littrace is loading
startup-finish = littrace is ready

# Reader evidence-Q&A panel. Answers come from the local littrace service (U3).
reader-panel-title = Evidence Q&A
reader-panel-source-note = Answers from the local littrace service
reader-panel-placeholder = Ask about this paper
reader-panel-ask = Ask
reader-panel-example = For example: under which experimental conditions does this method hold?
reader-panel-wait = The previous question is still running; submit again when it finishes
reader-panel-empty = No question yet. Type one, or select text and use Check evidence.
reader-panel-stage-queued = Queued for the service
reader-panel-stage-searching = Retrieving evidence
reader-panel-stage-writing = Writing claims
reader-panel-stage-verifying = Verifying claims
reader-panel-gap = Gap
reader-panel-gap-hint = No original evidence with a locatable page answered this question; only the question is kept.
reader-panel-gap-warning = A note from the retrieval run: results may be incomplete. The evidence listed above still stands.
reader-panel-gap-truncated = More hits than this panel shows were returned; only the first few are listed.
reader-panel-gap-lineage = A coverage note about these lineage results: relations not listed are not claimed to be absent.
reader-panel-evidence = Evidence text
reader-panel-evidence-title = Evidence
reader-panel-guide = Cloud Q&A is not enabled
reader-panel-guide-cloud = Answers need cloud Q&A. Enable it in Settings → Models & costs, then ask again. There is no retry here: retrying without the key only fails the same way.
reader-panel-guide-settings = Open settings
reader-panel-jump-copied = Deep link copied:
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
section-reading-missing = Missing pages
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

# Settings page (U5). Keys are bare here; the build prefixes each key with
# "littrace-", which is why the pane's data-l10n-id values carry the prefix
# while t() lookups in plugin code do not.
prefs-pane-label = littrace
prefs-group-connection = Connection
prefs-group-connection-desc = Where the plugin finds the littrace local service.
prefs-group-integration = Integration
prefs-group-integration-desc = One switch per feature. Changes take effect on the next Zotero start.
prefs-group-pro = Pro
prefs-group-pro-desc = Optional commercial features. Nothing here runs without the commercial layer.
prefs-service-host = Service address
prefs-service-host-value = 127.0.0.1 (fixed, loopback only)
prefs-service-host-desc = littrace only talks to the local service; the host is not configurable.
prefs-connection-port = Port
prefs-connection-port-desc = Port of the littrace local service. Default 8765; a value outside 1-65535 is ignored and the default applies.
prefs-connection-health = Service health
prefs-health-checking = Checking…
prefs-health-online = Online and compatible
prefs-health-outdated = Reachable but not compatible
prefs-health-offline = Offline
prefs-refresh = Refresh
prefs-diagnostics-hint = Full diagnostics (service version, port, Ollama, last sync) are on the toolbar status dot's right-click menu.
prefs-integration-lineage-column = Lineage column
prefs-integration-lineage-column-desc = Registers the 「Lineage」 column in the item list. Restart Zotero to apply; the column chooser still controls each library tree.
prefs-integration-reading-column = Close-reading column
prefs-integration-reading-column-desc = Registers the 「Close reading」 column in the item list. Restart Zotero to apply.
prefs-integration-deeplink = Deep links
prefs-integration-deeplink-desc = Links inside the function tabs are handed to Zotero's own zotero: handling; the plugin never writes back to your library.
prefs-pro-status = Commercial layer
prefs-pro-not-running = Not running
prefs-pro-authorized = Authorized
prefs-pro-unauthorized = Not authorized
prefs-pro-unknown = Unknown
prefs-pro-activate = Activation
prefs-pro-activate-hint = Activation stays disabled until the commercial layer ships.
prefs-pro-cross-check = Cross-check report
prefs-pro-cross-check-desc = Orchestrated cross-verification with a disagreement report.
prefs-pro-parallel-driver = Parallel task driver
prefs-pro-parallel-driver-desc = Multi-task driver with a priority queue.
prefs-pro-needed = Pro required
prefs-platform-unsupported = This platform is not supported yet
prefs-platform-unsupported-desc = littrace currently targets Windows x64. docs/support-matrix.json is the authoritative platform list.
prefs-first-run-note = First run: start the local service and prepare the models. The service install is hundreds of MB and the first index build takes about ten minutes.

# Toolbar service status dot (U5).
status-dot-checking = Checking the littrace service…
status-dot-online = littrace service: online
status-dot-outdated = littrace service: reachable but not compatible
status-dot-offline = littrace service: offline
status-reason-unreachable = no response on the service port
status-reason-http = the port answered with an error
status-reason-malformed = the answer was not readable
status-reason-api-mismatch = the service speaks a different API version
status-reason-version-old = the service version is older than this plugin requires
status-reason-version-new = the service version is newer than this plugin supports
status-reason-unknown = unknown reason
status-menu-workbench = Open web workbench
status-menu-diagnostics = Diagnostics
status-diag-title = Service diagnostics
status-diag-checking = Checking…
status-diag-failed = Diagnostics could not run
status-diag-service-version = Service version
status-diag-requirement = Plugin minimum
status-diag-version-relation = Compatibility
status-diag-version-ok = within the supported range
status-diag-version-old = too old
status-diag-version-new = too new
status-diag-version-unknown = could not compare
status-diag-port = Port
status-diag-ollama = Ollama
status-diag-ollama-reachable = reachable
status-diag-ollama-unreachable = unreachable
status-diag-ollama-unknown = could not check
status-diag-last-sync = Last sync
status-diag-never-synced = never
status-diag-unavailable = unavailable
status-diag-refresh = Refresh
status-diag-failures = Failed checks
status-diag-sync-synced = synced
status-diag-sync-running = in progress
status-diag-sync-failed = failed
status-diag-sync-unknown = state unknown
