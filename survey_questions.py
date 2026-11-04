"""The founder-approved survey copy and its stable answer fields.

Classification remains available to research consumers; it does not add, remove, or
reorder these questions.
"""

SURVEY_VERSION = "2026-10"

OPENING_QUESTION = (
    "Hi! I'll help you put together a market-research report. "
    "To start: in as much detail as you have, what does your product do and who is it for?"
)

INTRODUCTION = (
    "Some additional questions will help us better understand your idea. No worries if "
    "you don't have the answer right now. Feel free to skip any question and we will "
    "estimate the answer from your earlier description."
)

GROUPS = (
    {"id": "core", "title": "Core Pack"},
    {"id": "specific", "title": "More Specific Questions"},
)

_COPY = (
    ("core", "status_quo",
     "What other product or service might your customer be already using? If yours is a brand-new idea, what else might fill your customer's time or needs?",
     "which alternatives the research should compare"),
    ("core", "monthly_cost_estimate",
     "Roughly what will it cost you to run each month? Feel free to give the end number, or the cost breakdown.",
     "the operating costs and break-even assumptions"),
    ("core", "customer_evidence",
     "Has anyone tried your product already? If so, what feedback have you gotten?",
     "what customer demand is already supported by feedback"),
    ("core", "named_competitors",
     "A bonus if you know one: which company does the closest thing to this?",
     "where competitor research starts"),
    ("core", "success_target",
     "If this works, what does the first year look like? A rough revenue figure or customer count you'd be happy with.",
     "the first-year outcome the analysis should evaluate"),
    ("core", "business_model",
     "What is your revenue model? In other words, how do you make money — pay per item, a subscription, free with ads? If you have multiple products or revenue streams, tell us about your flagship model.",
     "how each revenue stream should be understood"),
    ("core", "delivery_mode",
     "Is your business in person, online, or a mix of both?",
     "which delivery channels the analysis should cover"),
    ("specific", "geography",
     "Where is your business located? If it's in person, give us the location and the radius you think people would be willing to drive to get to you. If it's online, give us the regions you would want to service (local shipping, US, international, etc). If you have multiple locations, give us the primary or first one of interest.",
     "the locations and service regions being researched"),
    ("specific", "service_capacity",
     "How many people could your business service? Feel free to answer in any interval (daily, weekly, etc).",
     "the service volume and its stated time period"),
    ("specific", "scalability",
     "Could your business easily scale, or does it have fixed capacity? (Can you hire more workers, contract out, etc?)",
     "which operating constraints change as the business grows"),
    ("specific", "acquisition_channels",
     "How will your customer find you? If it's in person, will you rely on foot traffic, billboards, word of mouth? If online, will you be hosted on a platform (Amazon, Etsy, GitHub), appearing in search results, or have referrals?",
     "how customers discover and buy from the business"),
    ("specific", "unit_economics",
     "What is your marginal profit? What does your flagship product cost to make per item, and how much will it cost the consumer?",
     "the stated costs, selling prices, and margins without mixing them"),
    ("specific", "fixed_costs",
     "What are your fixed costs? Things like physical space, worker salaries, or monthly subscriptions.",
     "which costs recur independently of sales volume"),
    ("specific", "constraints",
     "Is there anything else we need to know? Rules, licenses, barriers, etc?",
     "the other constraints the research should consider"),
)

QUESTIONS = tuple(
    {"field": field, "question": question, "group": group, "input_kind": "para",
     "optional": True, "drives": drives,
     "consumer": "research brief and business-object PREP", "consumer_kind": "brief"}
    for group, field, question, drives in _COPY
)
FIELDS = tuple(question["field"] for question in QUESTIONS)
