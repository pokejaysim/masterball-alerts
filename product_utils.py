#!/usr/bin/env python3
"""Shared product, URL, discovery, and stock-result helpers."""

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


STOCK_IN = "in_stock"
STOCK_OUT = "out_of_stock"
STOCK_UNKNOWN = "unknown"
STOCK_BLOCKED = "blocked"
STOCK_PREORDER = "preorder"
STOCK_MARKETPLACE = "marketplace"

STOCK_STATUSES = {
    STOCK_IN,
    STOCK_OUT,
    STOCK_UNKNOWN,
    STOCK_BLOCKED,
    STOCK_PREORDER,
    STOCK_MARKETPLACE,
}

PROTECTED_RETAILERS = {"walmart", "pokemoncenter"}


@dataclass
class StockResult:
    status: str
    price: Optional[str] = None
    seller: Optional[str] = None
    reason: Optional[str] = None
    checked_at: Optional[str] = None

    def __post_init__(self):
        if self.status not in STOCK_STATUSES:
            raise ValueError(f"Unknown stock status: {self.status}")
        if not self.checked_at:
            self.checked_at = datetime.now().isoformat(timespec="seconds")

    @property
    def is_in_stock(self):
        return self.status == STOCK_IN

    @property
    def is_definitive_unavailable(self):
        return self.status in {STOCK_OUT, STOCK_PREORDER, STOCK_MARKETPLACE}

    @property
    def is_indeterminate(self):
        return self.status in {STOCK_UNKNOWN, STOCK_BLOCKED}

    def as_dict(self):
        return {
            "status": self.status,
            "price": self.price,
            "seller": self.seller,
            "reason": self.reason,
            "checked_at": self.checked_at,
        }

    @classmethod
    def in_stock(cls, price=None, seller=None, reason=None):
        return cls(STOCK_IN, price=price, seller=seller, reason=reason)

    @classmethod
    def out_of_stock(cls, reason=None, price=None, seller=None):
        return cls(STOCK_OUT, price=price, seller=seller, reason=reason)

    @classmethod
    def unknown(cls, reason=None, price=None, seller=None):
        return cls(STOCK_UNKNOWN, price=price, seller=seller, reason=reason)

    @classmethod
    def blocked(cls, reason=None):
        return cls(STOCK_BLOCKED, reason=reason)

    @classmethod
    def preorder(cls, reason=None, price=None):
        return cls(STOCK_PREORDER, price=price, reason=reason)

    @classmethod
    def marketplace(cls, seller=None, reason=None, price=None):
        return cls(STOCK_MARKETPLACE, price=price, seller=seller, reason=reason)


def escape_html(value):
    return html.escape(str(value or ""), quote=False)


def normalize_url(url):
    """Normalize product URLs for dedupe while preserving useful identifiers."""
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("m."):
        netloc = "www." + netloc[2:]

    path = re.sub(r"/+", "/", parsed.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")

    keep_params = {"sku", "variant", "selectedSellerId"}
    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if k in keep_params]
    query = urlencode(sorted(query_pairs))

    return urlunparse((scheme, netloc, path, "", query, ""))


def candidate_id(url):
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:8]


def retailer_from_url(url):
    host = urlparse(url or "").netloc.lower()
    if "amazon.ca" in host:
        return "amazon"
    if "walmart.ca" in host:
        return "walmart"
    if "bestbuy.ca" in host:
        return "bestbuy"
    if "costco.ca" in host:
        return "costco"
    if "ebgames.ca" in host or "gamestop.ca" in host:
        return "ebgames"
    if "pokemoncenter.com" in host:
        return "pokemoncenter"
    return "generic"


def retailer_display_name(retailer_or_url):
    retailer = retailer_from_url(retailer_or_url) if "://" in str(retailer_or_url) else retailer_or_url
    return {
        "amazon": "Amazon CA",
        "walmart": "Walmart CA",
        "bestbuy": "Best Buy CA",
        "costco": "Costco CA",
        "ebgames": "GameStop/EB Games CA",
        "pokemoncenter": "Pokemon Center CA",
        "generic": "Unknown Retailer",
    }.get(retailer, str(retailer_or_url))


def product_identifier(url):
    normalized = normalize_url(url)
    retailer = retailer_from_url(normalized)

    if retailer == "amazon":
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", normalized)
        return match.group(1) if match else None
    if retailer == "bestbuy":
        match = re.search(r"/(\d{7,})(?:$|[/?#])", normalized)
        return match.group(1) if match else None
    if retailer == "walmart":
        match = re.search(r"/([A-Z0-9]{8,}|\d{8,})(?:$|[/?#])", normalized)
        return match.group(1) if match else None
    if retailer == "costco":
        match = re.search(r"(?:product\.|/)(\d{7,})(?:\.html|$|[/?#])", normalized)
        return match.group(1) if match else None
    if retailer == "ebgames":
        match = re.search(r"/Games/(\d+)/", normalized)
        return match.group(1) if match else None
    if retailer == "pokemoncenter":
        match = re.search(r"/product/([^/?#]+)", normalized)
        return match.group(1) if match else None
    return None


def name_from_url(url):
    parsed = urlparse(url or "")
    path_parts = [p for p in parsed.path.split("/") if p]
    slug = ""
    if path_parts:
        for part in reversed(path_parts):
            if not re.fullmatch(r"[A-Z0-9]{8,}|\d{6,}", part, re.IGNORECASE):
                slug = part
                break
    slug = re.sub(r"\.(html|aspx)$", "", slug, flags=re.IGNORECASE)
    words = re.sub(r"[-_]+", " ", slug).strip()
    words = re.sub(r"\s+", " ", words)
    return words.title() if words else "Pokemon TCG Product"


def product_name_for_candidate(raw_name, url):
    name = re.sub(r"\s+", " ", str(raw_name or "").strip())
    if not name or len(name) < 4:
        name = name_from_url(url)
    retailer = retailer_display_name(retailer_from_url(url))
    suffix = f" - {retailer}"
    if not name.lower().endswith(suffix.lower()):
        name = f"{name}{suffix}"
    return name


def default_priority(name, url=None, source=None):
    text = " ".join(str(v or "") for v in [name, url, source]).lower()
    high_tokens = [
        "elite trainer",
        " etb",
        " upc",
        "ultra premium",
        "super premium",
        " spc",
        "booster bundle",
        "pokemon center",
        "costco",
        "preorder",
        "pre-order",
    ]
    return "high" if any(token in text for token in high_tokens) else "normal"


def is_pokemon_tcg_sealed_candidate(name, url=""):
    text = f"{name or ''} {url or ''}".lower()

    if any(token in text for token in ["topps", "basketball", "football", "hockey", "baseball"]):
        return False

    has_pokemon = any(token in text for token in ["pokemon", "pokémon", "pok-mon", "pokmon"])
    has_sealed_signal = any(
        token in text
        for token in [
            "booster box",
            "booster bundle",
            "booster pack",
            "sleeved booster",
            "elite trainer",
            "trainer box",
            " etb",
            "ultra premium",
            "super premium",
            "premium collection",
            "special collection",
            "poster collection",
            "binder collection",
            "figure collection",
            "illustration collection",
            "tournament collection",
            "collection box",
            "collector chest",
            "mini tin",
            " pok ball",
            " poke ball",
            "pokeball",
            "blister",
            "build and battle",
            "battle deck",
            "expansion pack",
            "display box",
            "3 pack",
            "2 pack",
            "upc",
            "scarlet",
            "violet",
            "prismatic",
            "evolutions",
            "mega evolution",
        ]
    )
    negative = any(
        token in text
        for token in [
            "single card",
            "graded",
            "psa ",
            "cgc ",
            "shirt",
            "plush",
            "figure only",
            "video game",
            "nintendo switch",
            "code card",
            "acrylic",
            "display case",
            "sleeves",
            "deck box",
            "toploader",
            "top loader",
            "storage box",
            "protector",
            "holder",
            "compatible with pokemon",
        ]
    )
    return has_pokemon and has_sealed_signal and not negative


def stock_transition(previous_status, result):
    """Return the new boolean state and transition action for a StockResult."""
    previous = bool(previous_status)
    if result.is_in_stock:
        return True, "became_in_stock" if not previous else "still_in_stock"
    if result.is_definitive_unavailable:
        return False, "became_out_of_stock" if previous else "still_out_of_stock"
    return previous, "no_change"
