"""Submit and extract product reviews."""

from dataclasses import dataclass
from typing import List, Optional
import re

from playwright.sync_api import Page


@dataclass
class ReviewResult:
    success: bool
    rating: int
    nickname: str
    title: str
    detail: str
    error_message: Optional[str] = None


@dataclass
class Review:
    """A single customer review."""
    rating: int  # 1-5 stars
    title: str
    detail: str
    reviewer_name: str
    date: Optional[str] = None


@dataclass
class ReviewSummary:
    """Aggregate review statistics for a product."""
    average_rating: float  # e.g., 4.5
    total_reviews: int
    rating_breakdown: dict  # {5: 10, 4: 5, 3: 2, 2: 1, 1: 0}


def leave_product_review(
    page: Page,
    product_url: str,
    rating: int,
    nickname: str,
    title: str,
    detail: str,
) -> ReviewResult:
    """
    Open a product page and submit a review with the given rating (1-5 stars).
    """
    if rating < 1:
        rating = 1
    if rating > 5:
        rating = 5

    page.goto(product_url)
    page.wait_for_load_state("networkidle")
    
    # Reveal the reviews tab / form.
    reviews_tab = page.locator("a#tab-label-reviews-title")
    if reviews_tab.count() == 0:
        reviews_tab = page.locator("a.data.switch[href$='#reviews']")
    if reviews_tab.count() > 0:
        reviews_tab.first.click()

    # Select the desired star rating (inputs are ordered 1->5).
    rating_inputs = page.locator(".review-control-vote input.radio")
    if rating_inputs.count() >= rating:
        rating_inputs.nth(rating - 1).check()

    page.locator("input#nickname_field").fill(nickname)
    page.locator("input#summary_field").fill(title)
    page.locator("textarea#review_field").fill(detail)

    page.locator("form#review-form button.action.submit").click()
    page.wait_for_load_state("networkidle")
    
    error_loc = page.locator(
        ".page.messages .message-error, "
        ".page.messages .error.message, "
        "div.messages .message-error, "
        "div.messages .error.message"
    )
    if error_loc.count() > 0:
        msg = error_loc.nth(0).inner_text().strip() or "Unknown review error."
        return ReviewResult(
            success=False,
            rating=rating,
            nickname=nickname,
            title=title,
            detail=detail,
            error_message=msg,
        )

    success_loc = page.locator(
        ".page.messages .message-success, div.messages .message-success"
    )
    success = success_loc.count() > 0

    return ReviewResult(
        success=success,
        rating=rating,
        nickname=nickname,
        title=title,
        detail=detail,
        error_message=None if success else "Review submission may have failed.",
    )


def get_product_reviews(page: Page, product_url: str, max_reviews: Optional[int] = None) -> List[Review]:
    """
    Extract all customer reviews from a product page.
    
    Args:
        page: Playwright page instance
        product_url: URL of the product
        max_reviews: Maximum number of reviews to extract (None = all)
    
    Returns:
        List of Review objects
    """
    page.goto(product_url)
    page.wait_for_load_state("networkidle")
    
    # Click on reviews tab to load reviews
    reviews_tab = page.locator("a#tab-label-reviews-title")
    if reviews_tab.count() == 0:
        reviews_tab = page.locator("a.data.switch[href$='#reviews']")
    if reviews_tab.count() > 0:
        reviews_tab.first.click()
        page.wait_for_load_state("networkidle")
    
    reviews: List[Review] = []
    
    # Find all review items
    review_items = page.locator(".review-item, .product-review")
    count = review_items.count()
    
    if count == 0:
        return reviews
    
    # Limit to max_reviews if specified
    if max_reviews is not None:
        count = min(count, max_reviews)
    
    for i in range(count):
        item = review_items.nth(i)
        
        # Extract rating
        rating = 0
        rating_elem = item.locator(".rating-result, .review-ratings")
        if rating_elem.count() > 0:
            title_attr = rating_elem.first.get_attribute("title") or ""
            match = re.search(r"(\d+)", title_attr)
            if match:
                rating_percent = int(match.group(1))
                rating = round(rating_percent / 20)  # Convert 0-100 to 0-5
        
        # Extract title
        title_elem = item.locator(".review-title, .review-details-value")
        title = title_elem.first.inner_text().strip() if title_elem.count() > 0 else ""
        
        # Extract detail/content
        detail_elem = item.locator(".review-content, .review-details p")
        detail = detail_elem.first.inner_text().strip() if detail_elem.count() > 0 else ""
        
        # Extract reviewer name
        author_elem = item.locator(".review-author, .review-details-value strong")
        reviewer_name = author_elem.first.inner_text().strip() if author_elem.count() > 0 else "Anonymous"
        
        # Extract date (optional)
        date_elem = item.locator(".review-date, .review-details-value")
        date = None
        if date_elem.count() > 0:
            date_text = date_elem.first.inner_text().strip()
            # Try to extract date pattern
            date_match = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", date_text)
            if date_match:
                date = date_match.group(0)
        
        reviews.append(Review(
            rating=rating,
            title=title,
            detail=detail,
            reviewer_name=reviewer_name,
            date=date
        ))
    
    return reviews


def get_review_summary(page: Page, product_url: str) -> Optional[ReviewSummary]:
    """
    Get aggregated review statistics for a product.
    
    Args:
        page: Playwright page instance
        product_url: URL of the product
    
    Returns:
        ReviewSummary object or None if no reviews
    """
    page.goto(product_url)
    page.wait_for_load_state("networkidle")
    
    # Try to find review summary on product page
    # Average rating
    rating_elem = page.locator(".product-reviews-summary .rating-result, .rating-summary .rating-result")
    average_rating = 0.0
    
    if rating_elem.count() > 0:
        title_attr = rating_elem.first.get_attribute("title") or ""
        match = re.search(r"(\d+)", title_attr)
        if match:
            rating_percent = int(match.group(1))
            average_rating = rating_percent / 20.0  # Convert 0-100 to 0-5
    
    # Total review count
    reviews_link = page.locator(".product-reviews-summary .reviews-actions a, .rating-summary a")
    total_reviews = 0
    
    if reviews_link.count() > 0:
        text = reviews_link.first.inner_text()
        match = re.search(r"(\d+)", text)
        if match:
            total_reviews = int(match.group(1))
    
    # If no reviews, return None
    if total_reviews == 0:
        return None
    
    # Try to get rating breakdown (if available on page)
    rating_breakdown = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    
    # Note: Rating breakdown is not always available without going to reviews tab
    # For now, return basic summary
    
    return ReviewSummary(
        average_rating=average_rating,
        total_reviews=total_reviews,
        rating_breakdown=rating_breakdown
    )
