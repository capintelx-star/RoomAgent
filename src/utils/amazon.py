"""Amazon Associates affiliate link builder."""
import urllib.parse

from config import AMAZON_ASSOCIATE_TAG


def affiliate_search_link(item: str) -> str:
    """
    Return an Amazon search URL for the given item with the affiliate tag embedded.

    Example:
        affiliate_search_link("dish soap")
        → "https://www.amazon.com/s?k=dish+soap&tag=yourtag-20"
    """
    encoded = urllib.parse.quote_plus(item)
    return f"https://www.amazon.com/s?k={encoded}&tag={AMAZON_ASSOCIATE_TAG}"
