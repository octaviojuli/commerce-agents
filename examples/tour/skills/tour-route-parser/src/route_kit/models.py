"""The route content contract: one itinerary attachment read into fields.

Every fact carries ``cite``: the evidence unit ids (see ``segment.Unit``) it was read from.
A missing fact stays empty or ``unknown``; nothing is filled from general knowledge.
Two groups of models exist: ``*Out`` models are what the model is asked to return (kept
flat for tool calling), and ``RouteContent`` is the validated, assembled document.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

SCHEMA = "route-kit/1"


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


Cite = list[int]

NodeType = Literal[
    "meet",
    "transport",
    "poi",
    "group",
    "activity",
    "free",
    "shopping",
    "optional",
    "package",
    "recommend",
    "photo_spot",
    "notice",
]
Inclusion = Literal[
    "included", "gift", "optional_paid", "package_item", "recommended_not_included", "unknown"
]
VisitMode = Literal["inside", "outside", "passing", "drive_by", "distant_view", "walk", "unknown"]
Ticket = Literal["included", "excluded", "free_entry", "unknown"]
PartOfDay = Literal["morning", "noon", "afternoon", "evening", "night"]
TransportMode = Literal[
    "coach",
    "flight",
    "train",
    "night_train",
    "ferry",
    "cruise",
    "boat",
    "speedboat",
    "seaplane",
    "shuttle",
    "cable_car",
    "walk",
    "unknown",
]


class Note(_M):
    text: str
    cite: Cite = Field(default_factory=list)
    auto: SkipJsonSchema[bool] = (
        False  # risky source text attached as-is by code, not arranged by the model
    )


class Condition(_M):
    condition: str = ""
    text: str
    cite: Cite = Field(default_factory=list)


class Disclaimer(_M):
    reason: Literal[
        "weather", "wildlife", "operating_days", "closure", "booking", "traffic", "other"
    ] = "other"
    text: str
    cite: Cite = Field(default_factory=list)


class Transport(_M):
    mode: TransportMode = "unknown"
    from_place: str = ""
    to_place: str = ""
    distance_text: str = ""
    duration_text: str = ""
    service_no: str = ""
    times_text: str = ""
    reference: bool = False
    departure_local_time: str = ""
    arrival_local_time: str = ""
    arrival_day_offset: int | None = None


class ChildNode(_M):
    node_id: str = ""
    name: str
    local_name: str = ""
    visit_mode: VisitMode = "unknown"
    duration_text: str = ""
    ticket: Ticket = "unknown"
    includes: list[str] = Field(default_factory=list)
    inclusion: Inclusion = "unknown"
    description: str = ""
    cite: Cite = Field(default_factory=list)


class Node(ChildNode):
    type: NodeType
    part_of_day: PartOfDay | None = None
    clock_text: str = ""
    highlight: bool = False
    spot_label: str = ""
    price_text: str = ""
    min_participants: int | None = None
    booking_note: str = ""
    transport: Transport | None = None
    shopping_kind: (
        Literal["designated_store", "outlet_mall", "department_store", "market"] | None
    ) = None
    categories: list[str] = Field(default_factory=list)
    children: list[ChildNode] = Field(default_factory=list)
    alternative: Condition | None = None
    disclaimer: Disclaimer | None = None
    extra_cost_note: Note | None = None
    review: list[str] = Field(default_factory=list)


class Meal(_M):
    status: Literal["included", "self", "not_applicable", "unknown"] = "unknown"
    text: str = ""
    cite: Cite = Field(default_factory=list)


class Meals(_M):
    breakfast: Meal = Field(default_factory=Meal)
    lunch: Meal = Field(default_factory=Meal)
    dinner: Meal = Field(default_factory=Meal)


class Stay(_M):
    kind: Literal["hotel", "ship", "flight", "train", "home", "unknown"] = "unknown"
    names: list[str] = Field(default_factory=list)
    city: str = ""
    grade_text: str = ""
    grade_basis: Literal["supplier_description", "official_rating", "unknown"] = "unknown"
    room_type: str = ""
    consecutive_nights: int | None = Field(default=None, ge=1)
    or_similar: bool = False
    check_in_text: str = ""
    cite: Cite = Field(default_factory=list)


class Services(_M):
    coach: Literal["included", "excluded", "partial", "unknown"] = "unknown"
    guide: Literal["included", "excluded", "partial", "unknown"] = "unknown"
    note: str = ""
    cite: Cite = Field(default_factory=list)


class PortCall(_M):
    port: str = ""
    arrive_text: str = ""
    depart_text: str = ""
    all_aboard_text: str = ""
    cite: Cite = Field(default_factory=list)


class DayOut(_M):
    """What one day call returns."""

    title: str = ""
    cities: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    summary: str = ""
    day_kind: Literal[
        "regular", "transit", "free", "at_sea", "port_call", "cruising_no_landing", "resort"
    ] = "regular"
    travel_text: str = ""
    services: Services | None = None
    port_call: PortCall | None = None
    items: list[Node] = Field(default_factory=list)
    meals: Meals = Field(default_factory=Meals)
    stay: Stay = Field(default_factory=Stay)
    notes: list[Note] = Field(default_factory=list)
    title_cite: Cite = Field(default_factory=list)


class Day(DayOut):
    day_id: str = ""
    day: int
    day_end: int | None = None
    units: list[int] = Field(default_factory=list)
    overview_units: list[int] = Field(default_factory=list)
    status: Literal["extracted", "source_only"] = "extracted"
    issues: list[dict] = Field(default_factory=list)


class Item(_M):
    text: str
    category: str = ""
    cite: Cite = Field(default_factory=list)
    auto: SkipJsonSchema[bool] = (
        False  # risky source text attached as-is by code, not arranged by the model
    )


class Price(_M):
    category: Literal[
        "tour", "child", "senior", "single_room", "service_fee", "additional", "unknown"
    ] = "unknown"
    audience: str = ""
    condition: str = ""
    label: str = ""
    amount: str = ""
    currency: str = ""
    basis: str = ""
    text: str
    cite: Cite = Field(default_factory=list)


class Highlight(_M):
    title: str = ""
    text: str
    cite: Cite = Field(default_factory=list)


class NoticeGroup(_M):
    category: Literal[
        "visa_documents",
        "health_age",
        "booking_cancellation",
        "flight_luggage",
        "safety",
        "local_customs",
        "money_tips",
        "shopping_optional",
        "other",
    ] = "other"
    title: str = ""
    items: list[Item] = Field(default_factory=list)


class OptionalItem(_M):
    name: str
    price_text: str = ""
    duration_text: str = ""
    note: str = ""
    cite: Cite = Field(default_factory=list)


class ShoppingStop(_M):
    name: str
    kind: Literal["designated_store", "outlet_mall", "department_store", "market"] | None = None
    categories: list[str] = Field(default_factory=list)
    duration_text: str = ""
    cite: Cite = Field(default_factory=list)


class Policies(_M):
    single_room: list[Item] = Field(default_factory=list)
    child: list[Item] = Field(default_factory=list)
    tips: list[Item] = Field(default_factory=list)
    cancellation: list[Item] = Field(default_factory=list)
    deposit: list[Item] = Field(default_factory=list)


class CitedFact(_M):
    raw: str = ""
    cite: Cite = Field(default_factory=list)


class Applicability(CitedFact):
    start: date | None = None
    end: date | None = None
    departure_cities: list[str] = Field(default_factory=list)
    version_label: str = ""


class Formation(CitedFact):
    minimum_travelers: int | None = Field(default=None, ge=1)
    failure_action: str = ""
    booking_deadline: str = ""


class PassengerRule(CitedFact):
    minimum_age: int | None = Field(default=None, ge=0, le=120)
    maximum_age: int | None = Field(default=None, ge=0, le=120)
    bed_policy: str = ""
    conditions: str = ""


class VisaRule(CitedFact):
    type: str = ""
    submission_deadline: str = ""
    passport_validity: str = ""


class Insurance(CitedFact):
    included: bool | None = None
    description: str = ""


class TravelerRequirements(_M):
    child: PassengerRule = Field(default_factory=PassengerRule)
    senior: PassengerRule = Field(default_factory=PassengerRule)
    pregnancy: CitedFact = Field(default_factory=CitedFact)
    visa: VisaRule = Field(default_factory=VisaRule)
    insurance: Insurance = Field(default_factory=Insurance)


class Money(CitedFact):
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = ""
    unit: Literal["person", "room", "night"] | None = None


class CancellationTier(CitedFact):
    days_before_min: int | None = Field(default=None, ge=0)
    days_before_max: int | None = Field(default=None, ge=0)
    penalty_percent: Decimal | None = Field(default=None, ge=0, le=100)
    penalty_money: Money | None = None


class MealCounts(CitedFact):
    """The meals the attachment says the whole trip includes ("4 早 7 正餐"), as written."""

    breakfast: int | None = None
    main: int | None = None


class Meeting(CitedFact):
    location: str = ""
    time: str = ""
    domestic_connection: str = ""


class RouteOut(_M):
    """What the route-level call returns (cover and terms)."""

    title: str = ""
    subtitle: str = ""
    title_cite: Cite = Field(default_factory=list)
    nights: int | None = None
    depart_city: str = ""
    countries: list[str] = Field(default_factory=list)
    cover_facts: list[Item] = Field(default_factory=list)
    selling_points: list[Item] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    prices: list[Price] = Field(default_factory=list)
    departure_dates_text: str = ""
    departure_dates_cite: Cite = Field(default_factory=list)
    inclusions: list[Item] = Field(default_factory=list)
    exclusions: list[Item] = Field(default_factory=list)
    shopping: list[ShoppingStop] = Field(default_factory=list)
    optional_items: list[OptionalItem] = Field(default_factory=list)
    policies: Policies = Field(default_factory=Policies)
    applicability: Applicability = Field(default_factory=Applicability)
    formation: Formation = Field(default_factory=Formation)
    traveler_requirements: TravelerRequirements = Field(default_factory=TravelerRequirements)
    cancellation_tiers: list[CancellationTier] = Field(default_factory=list)
    meeting: Meeting = Field(default_factory=Meeting)
    shopping_status: Literal["present", "none", "unknown"] = "unknown"
    shopping_cite: Cite = Field(default_factory=list)
    shopping_quote: str = ""  # the words of the cited line that say there is no shopping
    meal_counts: MealCounts = Field(default_factory=MealCounts)
    notices: list[NoticeGroup] = Field(default_factory=list)


class Source(_M):
    file_name: str
    media: str
    pages: int | None = None
    sha256: str
    image_pages: list[int] = Field(default_factory=list)
    scanned: bool = False  # no text layer: every word came from page pictures
    pictures: list[dict] = Field(
        default_factory=list
    )  # {key, page, kind, lines, error, cover}: one per picture read (vision)
    cover_image: str = ""  # file name of the cover picture saved beside the JSON
    parser: str
    model: str = ""
    extracted_at: str = ""


class Quality(_M):
    days_expected: int | None = None
    days_found: int = 0
    days_extracted: int = 0
    units: int = 0
    mapped_units: int = 0  # direct + inferred + auto_attached
    direct_units: int = 0  # cited by a field, or a day header
    inferred_units: int = 0  # continuation sentences and short section labels
    auto_attached: int = 0  # risky units attached as-is to day notes or notices
    unmapped: list[dict] = Field(default_factory=list)
    issues: list[dict] = Field(default_factory=list)
    removed: list[dict] = Field(default_factory=list)
    image_units: int = 0  # evidence units transcribed from pictures
    confidence: float | None = None  # 0-100 triage score, see confidence.py
    review_reasons: list[str] = Field(default_factory=list)  # why a person should look first


class RouteContent(RouteOut):
    schema_: Literal["route-kit/1"] = Field(default=SCHEMA, alias="schema")
    code: str = ""
    listed_name: str = ""
    days_count: int = 0
    days: list[Day] = Field(default_factory=list)
    source: Source
    quality: Quality = Field(default_factory=Quality)
    units: list[dict] = Field(
        default_factory=list
    )  # {id, text, page, line, origin}: evidence for review views; origin "image" = picture

    model_config = ConfigDict(extra="ignore", populate_by_name=True, serialize_by_alias=True)
