"""The agent mesh. Importing this package registers every tool contract."""

from neutail.agents import care, concierge, muse, persona, sentry, stubs, tailor, tally  # noqa: F401

AGENTS = {

    "persona_agent": ("PERSONA", "UC1 profiling & segmentation"),

    "muse_agent": ("MUSE", "UC2 personalised discovery"),

    "tailor_agent": ("TAILOR", "UC3 size & fit"),

    "care_agent": ("CARE", "UC4 autonomous service (hosts CONCIERGE)"),

    "concierge_agent": ("CONCIERGE", "UC4 upsell & monetisation"),

    "sentry_agent": ("SENTRY", "UC4 checkout risk & approval"),

    "tally_agent": ("TALLY", "UC5 loyalty & gamification"),

    "onebasket_agent": ("ONEBASKET", "Session & identity (stub)"),

    "scout_agent": ("SCOUT", "Trend & demand signal (stub)"),

    "pace_agent": ("PACE", "Price context, ±5% guardrail (stub)"),

    "grade_agent": ("GRADE", "Returns grading → TAILOR (stub)"),

}