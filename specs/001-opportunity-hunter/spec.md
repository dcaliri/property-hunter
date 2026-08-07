# Feature Specification: Property Opportunity Hunter

**Feature Branch**: `001-opportunity-hunter`

**Created**: 2026-08-06

**Status**: Draft

**Input**: User description: "Build an AI system, production-deployable, that scans real estate sites (starting with one: inmoup.com.ar, Argentina), extracts, analyzes and stores information, computes sales and rent price analyses by zone, detects potential opportunities, and alerts the user via email and/or other channels. Use SDD with spec-kit."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Collect and store property listings (Priority: P1)

The system periodically scans inmoup.com.ar for property listings (for sale and for rent) within the configured geographic area of Argentina (e.g., Capital Federal/CABA, Mendoza, Córdoba). For each listing it captures the key facts — address, neighborhood (barrio) and locality, asking price in US dollars, home type, size (bedrooms, bathrooms, covered/total m²), listing URL, and the listing's unique identifier as published by the site. Each captured listing is stored with a reference to where it came from and when it was retrieved. When the same property is seen again on a later scan, the system updates it instead of storing a duplicate, and records any change in asking price as part of the property's price history.

**Why this priority**: Everything downstream — zone analysis, opportunity detection, alerts — depends on a reliable, complete, current store of listings. Without this foundation nothing else works.

**Independent Test**: Can be fully tested by running a scan against a controlled set of listing fixtures and verifying that (a) every listing is persisted with correct fields and provenance, (b) re-running the scan with an unchanged price produces no duplicate records, and (c) re-running with a changed price records a price history entry. This delivers a reliable listing database as the first valuable increment.

**Acceptance Scenarios**:

1. **Given** a configured scan area with known listing fixtures, **When** the system runs a collection pass, **Then** every fixture listing is stored with its address, price, attributes, source URL, retrieval time, and raw payload reference.
2. **Given** a listing already stored with price $400,000, **When** a later pass sees the same listing at $380,000, **Then** the system updates the current price and appends a price-history entry (old $400,000, new $380,000, observation date), with no duplicate property record.
3. **Given** a listing no longer present in a scan (delisted), **When** the pass completes, **Then** the property is marked as inactive/delisted while its historical records remain intact.

---

### User Story 2 - Compute zone baselines and market value estimates (Priority: P1)

The system groups stored listings into geographic zones and computes per-zone market baselines: for each zone, a typical price per square meter, median asking price, and median asking rent, each with the number of observations and the time window they cover. Baselines are recalculated after each collection pass and distinguish sales data from rental data. Zones with too few observations are flagged as "insufficient data" rather than producing misleading averages.

In addition, the system trains a machine-learning valuation model on the collected sale listings: the model learns how asking price relates to property attributes (home type, bedrooms, bathrooms, covered and total area, neighborhood, and age) and then produces a market value estimate for every active for-sale property. Each estimate records the model version that produced it and the model's measured quality, so the user can judge its trustworthiness. When the collected data is too small to train a reliable model, the system falls back to a simple price-per-m² estimate for the property's zone and says so explicitly.

**Why this priority**: Opportunity detection (User Story 3) is only meaningful when each listing can be compared against a defensible local baseline for its zone — and an ML value estimate is more accurate than a zone average because it also accounts for the property's own attributes.

**Independent Test**: Can be fully tested by loading a known set of stored listings across two zones and verifying the computed medians, price-per-m² values, observation counts, and that a zone with a single listing is flagged as insufficient data; and by training the valuation model on a small controlled dataset and verifying it produces a value estimate per active listing with a recorded model version and quality metrics, and falls back cleanly when data is insufficient. This delivers a usable zone-pricing report plus per-property value estimates as a standalone increment.

**Acceptance Scenarios**:

1. **Given** stored listings across two zones with known prices and rents, **When** baselines are recomputed, **Then** each zone has a sales baseline and a rental baseline with correct median values and observation counts.
2. **Given** a zone with fewer than the minimum observation threshold, **When** baselines are recomputed, **Then** that zone is flagged as insufficient data and excluded from opportunity scoring for that period.
3. **Given** a new collection pass with fresh listings, **When** baselines are recomputed, **Then** the stored baseline is replaced by the new computation with its new time window, preserving the previous baseline as history.
4. **Given** a training dataset with enough sale listings, **When** the valuation model is trained, **Then** every active for-sale listing receives a value estimate and the model version and quality metrics (error and fit) are recorded with it.
5. **Given** too few sale listings to train reliably, **When** valuation runs, **Then** the system falls back to the zone price-per-m² estimate for each listing and marks the fallback in the record.

---

### User Story 3 - Detect potential opportunities (Priority: P2)

The system evaluates every active for-sale listing against its zone baseline and value estimate and produces a set of opportunity signals per listing: (a) priced below its **model-estimated market value** by a threshold, (b) expected annual rent (from zone rental baseline) relative to asking price above a yield threshold, (c) recent asking-price reduction. Rules and thresholds are configurable — the user can enable or disable each signal and set its threshold. A listing is a "potential opportunity" when its enabled signals match. Each detection records the signals that fired and the underlying numbers — including the model's value estimate and version for the undervaluation signal — so the user can judge the reasoning.

**Why this priority**: This is the core intelligence of the product — converting raw data, baselines, and learned value estimates into actionable, explainable candidate opportunities.

**Independent Test**: Can be fully tested with stored fixtures and baselines where a listing is (a) priced 15% below its model-estimated value, (b) priced for 9% yield, (c) reduced 6% in the last 30 days, and verifying each configurable rule fires correctly and the detection record contains the exact numbers, the model prediction, and the reasoning. This delivers explainable, model-informed opportunity scoring as a standalone increment.

**Acceptance Scenarios**:

1. **Given** a listing priced 15% below its model-estimated value, **When** detection runs with the undervaluation signal enabled at 10%, **Then** the listing is flagged with the undervaluation signal, the model's value estimate and version, and the supporting numbers.
2. **Given** a rule set with only the price-drop signal enabled, **When** detection runs, **Then** only listings with a qualifying recent price reduction are flagged, regardless of undervaluation or yield.
3. **Given** a detection created for a listing, **When** a later pass changes the listing price, value estimate, or baseline, **Then** a new detection supersedes the old one, and the old one remains as history.

---

### User Story 4 - Alert the user on new opportunities (Priority: P2)

After each detection pass, the system notifies the user of newly identified opportunities. Notifications are delivered by email (primary channel), in a single digest per run, listing each opportunity with its property address, asking price, the signals that fired, the relevant zone baseline and value-estimate numbers, and a link to the listing. When an AI language service (LLM) is configured, the digest also opens with a short plain-language summary of the day's opportunities and includes enriched amenity/condition tags extracted from each listing's description. The system confirms deliveries: a failed notification is retried with backoff and reported if it ultimately cannot be delivered. The notification mechanism is designed so additional channels (e.g., SMS, messaging apps) can be added without rework.

**Why this priority**: The value of detection is only realized when opportunities reach the user reliably. Email-first keeps the MVP simple while the design allows more channels later.

**Independent Test**: Can be fully tested by triggering a detection pass with one known new opportunity and verifying the user receives a correctly formatted digest with that property, and that a deliberately failed delivery is retried and then flagged. This delivers the end-to-end alerting value as a standalone increment.

**Acceptance Scenarios**:

1. **Given** one newly detected opportunity, **When** the notification pass runs, **Then** the user receives one digest email containing that property's address, price, signals, baseline context, and listing link.
2. **Given** a previously alerted opportunity that is still flagged, **When** a later notification pass runs, **Then** it is not re-sent (no duplicate alerts for the same detection).
3. **Given** a notification that cannot be delivered on the first attempt, **When** delivery fails, **Then** it is retried with backoff, and if it still fails it is surfaced as a failed alert in operations reporting.

---

### Edge Cases

- What happens when inmoup.com.ar returns no results for the configured area (e.g., empty or zero-result page)?
- How does the system handle a listing whose price is absent or malformed?
- How does the system handle a listing that cannot be mapped to a zone? Listings with no parseable barrio are bucketed under a synthetic "unknown" zone within their region (e.g., "unknown, Capital Federal"), included in baseline counts, and logged so the gap is visible. If the region itself is missing, the listing is stored but excluded from baseline computation and flagged in the run report. Listings bucketed under "unknown" still count toward SC-002 zone-assignment coverage.
- How does the system react when the source site changes its page structure or blocks the scanner?
- What happens if a single scan run is interrupted halfway (partial results)?
- How are duplicate listings that appear in both sale and rental sections handled?
- What happens when the zone baseline observation count is exactly at the threshold?
- What happens when there is not enough sale data to train the valuation model reliably? (The system falls back to a zone price-per-m² estimate and marks the fallback.)
- What happens if the AI language service is unavailable or not configured? (Optional features are skipped; the pipeline and templated digest still complete normally.)
- What happens when the valuation model is trained on stale or very different data? (Model version and training window are recorded with every prediction so outdated estimates are identifiable.)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST periodically collect property listings from inmoup.com.ar within a configured geographic area of Argentina, covering both for-sale and for-rent listings.
- **FR-002**: The system MUST extract, for each listing, at minimum: address, neighborhood/barrio, locality, current asking price (US dollars) or rent, home attributes (beds, baths, covered/total m², type), listing URL, and the listing's unique identifier as published by the source.
- **FR-002b**: The system MUST record the property's exact map-marker coordinates (latitude/longitude) with each listing and derive the zone (barrio) from the property's own locality as published by the source — never from the agency's office address.
- **FR-003**: The system MUST persist every listing with provenance: source, original URL, retrieval timestamp, and a reference to the raw data as retrieved.
- **FR-004**: The system MUST identify a stable identity per property (using the source listing identifier) so repeat observations update the same record rather than creating duplicates.
- **FR-005**: The system MUST record asking-price changes over time as an append-only price history per property.
- **FR-006**: The system MUST mark properties as inactive when they disappear from the source, without deleting historical data.
- **FR-007**: The system MUST assign each property to a geographic zone (neighborhood/barrio within locality) and group sales data separately from rental data.
- **FR-008**: The system MUST compute per-zone market baselines (sales and rental) including median price, median rent, and median price-per-m², with observation counts and a time window.
- **FR-009**: The system MUST flag zones with insufficient observations instead of emitting unreliable baselines.
- **FR-010**: The system MUST support user-configurable opportunity rules: (a) below-market pricing threshold against the model-estimated market value (per FR-021), (b) rental-yield threshold, (c) recent price-reduction threshold — each independently enableable with adjustable thresholds.
- **FR-011**: The system MUST produce an explainable detection record per flagged opportunity containing the signals that fired and the underlying numbers.
- **FR-012**: The system MUST deliver a digest notification of new opportunities to the user via email, including property, price, signals, baseline context, and listing link.
- **FR-013**: The system MUST NOT re-notify the user about an opportunity that was already alerted for the same detection state.
- **FR-014**: The system MUST retry failed notification deliveries with backoff and surface deliveries that ultimately fail.
- **FR-015**: The system MUST run autonomously on a schedule and support manual re-runs and backfills without corrupting stored data.
- **FR-016**: The system MUST collect data from inmoup.com.ar in a polite, low-frequency manner that respects the site's terms and `robots.txt`: throttled, low-concurrency requests, an identifiable user-agent, and no action that imposes a disproportionate load on the source. Collection is for the user's personal investment research only; data obtained must not be commercially republished or redistributed, in accordance with the source's terms of service.
- **FR-017**: The system MUST log all pipeline activity in structured form and expose success/failure metrics per run.
- **FR-018**: The system MUST train a machine-learning valuation model on active sale listings that learns asking price from property attributes (home type, bedrooms, bathrooms, covered/total area, neighborhood, age), and MUST use it to estimate the market value of every active for-sale listing.
- **FR-019**: The system MUST record, with every value estimate, the model version, the model's quality metrics, and whether the estimate came from the trained model or from the fallback price-per-m² heuristic.
- **FR-020**: The system MUST retrain the valuation model on a schedule (after successful collection passes when new sale data exists) and MUST fall back to the zone price-per-m² heuristic whenever the training dataset is too small or training fails, without interrupting the pipeline.
- **FR-021**: The undervaluation opportunity signal MUST be computed against the model-estimated market value (or the fallback estimate when no model is available), not against a single zone average.
- **FR-022**: When an AI language service (LLM) is configured, the system MUST extract structured amenity and condition tags from listing descriptions and MUST open each digest with a short plain-language summary of the day's opportunities; when it is not configured or unavailable, these features MUST be skipped without failing the run.

### Key Entities

- **Property**: A real-estate unit identified by a stable identity (source listing identifier); attributes include home type, beds, baths, covered/total m², current status, current asking price, zone assignment.
- **Listing**: An observed offering of a property on the source at a point in time; includes asking price/rent (US dollars), URL, source listing identifier, observed-at timestamp, source, raw payload reference, current/active flag.
- **Price History Entry**: A record of a change in a property's asking price, with old value, new value, and observation date.
- **Zone**: A geographic area (neighborhood/barrio within a locality) used for grouping; contains computed baselines over time.
- **Zone Baseline**: Aggregated market statistics for a zone over a time window, computed separately for sales and rentals (median price, median rent, median price-per-m², observation count, window).
- **Model Version**: A versioned, persisted instance of the trained valuation model, recording its training window, dataset size, and quality metrics (fit and error).
- **Value Estimate**: The ML-predicted market value for a property (or the fallback price-per-m² estimate), referencing the model version, quality metrics, and whether it was a fallback.
- **Opportunity Signal**: A rule evaluation result for a property (undervaluation, yield, price-drop), each with the threshold applied and the observed/expected values; the undervaluation signal references the property's value estimate.
- **Detection**: A recorded conclusion that a property is a potential opportunity, referencing the signals that fired and the baseline snapshot used; has status and timestamps.
- **Notification**: A delivery of a digest (or individual opportunity) to the user over a channel, with delivery status, attempt count, and channel.
- **Alert Preference**: User configuration: enabled signal rules, thresholds, notification channel(s), and delivery address.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A full collection pass for the configured area completes and persists all discovered listings without duplicates — verified by zero duplicate property identities across repeated passes.
- **SC-002**: At least 95% of collected listings are assigned to a valid zone and included in a baseline computation within one pass of collection.
- **SC-003**: Zone baselines are computed for every zone with sufficient data in under one minute after a collection pass completes.
- **SC-004**: Opportunity detections reference a baseline snapshot and list the exact numbers behind every signal fired (100% of detections are explainable).
- **SC-005**: Newly detected opportunities are delivered to the user in a digest within 10 minutes of the detection pass completing.
- **SC-006**: No duplicate alert is delivered for the same detection state (0 duplicates under normal operation).
- **SC-007**: Failed notification deliveries are retried and surfaced; 100% of delivery failures are visible in operations reporting.
- **SC-008**: The system runs unattended on schedule; a crash or partial run does not corrupt stored data and a manual re-run recovers to a consistent state.
- **SC-009**: Every active for-sale listing receives a value estimate from a recorded model version (or an explicit fallback) with quality metrics; 0% of active listings lack a value estimate or a recorded fallback after a completed run.
- **SC-010**: The undervaluation signal is computed against the model value estimate (not a zone average); 100% of undervaluation detections reference the estimate and its version/quality, and training failures or insufficient data never stop the pipeline (fallback used instead).
- **SC-011**: With an AI language service configured, the digest includes a short plain-language opening summary and enriched amenity/condition tags per opportunity; without configuration, the digest renders fully without these additions and the run reports the feature as skipped.

## Clarifications

### Session 2026-08-06

- Q: Which real estate source site should the system scan first? → A: inmoup.com.ar (Argentina), not Zillow. Sales and rental listings, prices in US dollars, zones by neighborhood/barrio and locality.
- Q: What should define a "potential opportunity" that triggers an alert? → A: Configurable combination of signals (below-market pricing threshold, rental-yield threshold, recent price-reduction threshold), each independently enableable with adjustable thresholds.
- Q: How should data be collected from inmoup.com.ar given its terms of service? → A: Polite, low-frequency collection for personal use only, respecting `robots.txt` and the site's terms (no commercial exploitation, no disproportionate load). Recorded in FR-016.

## Assumptions

- The system starts with inmoup.com.ar (Argentina) as the single source, with the architecture allowing additional sources later.
- Collection from inmoup.com.ar is performed for the user's personal investment research: polite, low-frequency, throttled requests with an identifiable user-agent, respecting `robots.txt` and the site's terms of service (which permit personal use of site information and prohibit commercial exploitation and disproportionate load). Data is used internally and never republished commercially.
- The initial geographic scope is Capital Federal (CABA), Buenos Aires, with the same collection machinery able to target any region published by the site (Mendoza, Córdoba, etc.).
- The system scans once per day by default; frequency is configurable.
- Zones default to neighborhood/barrio granularity within the locality published by the source.
- Prices are quoted in US dollars (the site's default currency), so no currency conversion is needed in version 1.
- Notification channel starts with email; additional channels are future work but the design must not preclude them.
- The user is a single individual operator, not a multi-tenant service, in the first version.
- The system runs on a hosted schedule (cloud or always-on server) rather than on a consumer device.
- Accuracy matters more than raw speed: baselines and detections favor correctness and explainability over real-time updates.
- "AI system" is interpreted as: automated, intelligent analysis and scoring of market data, combining a machine-learning valuation model with statistical comparables and rules — plus optional generative-AI enrichment (description tags and digest narrative) when an external language service is configured.
- The valuation model uses a gradient-boosted regression algorithm (HistGradientBoostingRegressor) trained on the active sale listings themselves (self-supervised against asking price), with models persisted, versioned, and quality-scored after every retrain.
- The model is retrained after successful collection passes that add new sale data; predictions are always recomputed for active listings after each retrain or fallback decision.
- The AI language service is optional and external, reached through an OpenAI-compatible chat-completions endpoint; its base URL, model name, and API key are configuration/environment values. Without a configured key the feature is skipped gracefully.
- LLM output is treated as supplementary enrichment only: it never gates or determines opportunity detections, notifications, or pricing decisions.
- The LLM is called at most once per affected listing description per run (budgeted), with a short timeout, and failures are logged and skipped without failing the pipeline.
