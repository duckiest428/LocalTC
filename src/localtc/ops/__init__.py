"""Real-world operational data, from free public services: airline schedules (for the traffic at the gates), weather
(METAR, TAF, SIGMETs, radar), NOTAMs, temporary flight restrictions and restricted airspace, terrain, and charts
(ChartFox). Each source is cached by airport or region with its retrieval time and expiry, asked no more often than
its fair use allows, and kept as a snapshot for the replay; what isn't known is said to be unknown, never guessed.

Nothing here is used by the ATC directly (it never reaches the network): the app fetches, the ATC is told.
"""
