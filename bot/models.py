from dataclasses import dataclass
from typing import Optional


@dataclass
class Match:
    id: int
    chat_id: int
    message_id: Optional[int]
    kickoff_at: str
    date: str
    weekday: Optional[str]
    field: Optional[str]
    time_start: Optional[str]
    time_end: Optional[str]
    price: Optional[str]
    payment_info: Optional[str]
    location_url: Optional[str]
    max_players: int
    status: str
    reminder_24h_sent_at: Optional[str]
    reminder_2h_sent_at: Optional[str]
    auto_closed_at: Optional[str]
    created_at: str


@dataclass
class Registration:
    id: int
    match_id: int
    user_id: int
    contact: str
    contact_type: str
    display_name: str
    slot_type: str
    slot_number: int
    status: str
    joined_at: str
    main_since: Optional[str]
    cancelled_at: Optional[str]
