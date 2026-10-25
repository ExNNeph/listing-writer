"""Per-platform rules, enforced in code after the model has written the listing."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    name: str
    title_max: int  # characters
    tags_max: int  # 0 = the platform has no tags field
    tag_len_max: int
    style: str  # writing guidance passed to the model


PLATFORMS = {
    "vinted": Platform("Vinted", 60, 5, 20,  # ponytail: 60 is our choice for readable titles, not a Vinted limit
                       "casual and short; mention size, condition and how it fits or is worn; tags are hashtags"),
    "ebay": Platform("eBay", 80, 0, 0,  # eBay's title limit is 80 characters; item specifics instead of tags
                     "factual; lead the title with brand and item type; no hashtags, no emojis"),
    "etsy": Platform("Etsy", 140, 13, 20,  # Etsy: titles up to 140 characters, 13 tags of up to 20 characters
                     "warm and descriptive; materials, era or style, what makes it special; tags are search phrases"),
}
