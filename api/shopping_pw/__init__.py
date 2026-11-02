"""Magento storefront helpers split into focused modules."""

from . import constants
from .constants import (
    account_edit_url,
    address_book_url,
    advanced_search_url,
    base_url,
    cart_url,
    checkout_url,
    login_url,
    order_history_url,
    search_terms_url,
    wishlist_url,
)
from .config import (
    DEFAULT_CUSTOMER_EMAIL,
    DEFAULT_CUSTOMER_PASSWORD,
    get_default_customer_credentials,
)
from .shipping import Address, ShippingStepResult, complete_shipping_step
from .search import (
    AdvancedSearchQuery,
    ProductSummary,
    SearchTerm,
    advanced_search_and_extract_products,
    get_popular_search_terms,
    search_and_extract_products,
)
from .product import ProductDetails, extract_product_details
from .cart import (
    AddToCartResult,
    CartItem,
    add_product_to_cart,
    get_cart_items,
    set_cart_item_quantity,
    remove_cart_item,
    empty_cart,
)
from .order import (
    OrderSummary,
    OrderItem,
    OrderDetails,
    ReorderResult,
    get_order_history,
    get_order_details,
    reorder_order,
)
from .review import ReviewResult, leave_product_review
from .wishlist import (
    AddToWishlistResult,
    WishlistItem,
    add_product_to_wishlist,
    get_wishlist_items,
    set_wishlist_item_quantity,
    remove_wishlist_item,
    empty_wishlist,
)
from .account import AccountUpdateResult, update_account_info
from .address_book import AddressSaveResult, edit_address, add_address
from .compare import (
    AddToCompareResult,
    CompareRunResult,
    ComparedProduct,
    ComparedAttributeRow,
    ComparePageData,
    add_product_to_compare,
    open_compare_page,
    compare_products,
    extract_compare_page,
)
from .login import LoginResult, login_customer

def __getattr__(name: str) -> str:
    """Delegate the legacy uppercase URL constants to `constants`.

    Re-exporting them eagerly would snapshot the storefront URL at package
    import, which defeats the whole point of resolving it per call.
    """
    if name == "BASE_URL" or name in constants._PATHS:
        return getattr(constants, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BASE_URL",
    "CART_URL",
    "CHECKOUT_URL",
    "WISHLIST_URL",
    "ORDER_HISTORY_URL",
    "LOGIN_URL",
    "base_url",
    "cart_url",
    "checkout_url",
    "wishlist_url",
    "account_edit_url",
    "address_book_url",
    "order_history_url",
    "login_url",
    "advanced_search_url",
    "search_terms_url",
    "DEFAULT_CUSTOMER_EMAIL",
    "DEFAULT_CUSTOMER_PASSWORD",
    "get_default_customer_credentials",
    "Address",
    "ShippingStepResult",
    "complete_shipping_step",
    "AdvancedSearchQuery",
    "ProductSummary",
    "SearchTerm",
    "advanced_search_and_extract_products",
    "search_and_extract_products",
    "get_popular_search_terms",
    "ProductDetails",
    "extract_product_details",
    "AddToCartResult",
    "CartItem",
    "add_product_to_cart",
    "get_cart_items",
    "set_cart_item_quantity",
    "remove_cart_item",
    "empty_cart",
    "OrderSummary",
    "OrderItem",
    "OrderDetails",
    "ReorderResult",
    "get_order_history",
    "get_order_details",
    "reorder_order",
    "ReviewResult",
    "leave_product_review",
    "AddToWishlistResult",
    "WishlistItem",
    "add_product_to_wishlist",
    "get_wishlist_items",
    "set_wishlist_item_quantity",
    "remove_wishlist_item",
    "empty_wishlist",
    "AccountUpdateResult",
    "update_account_info",
    "AddressSaveResult",
    "edit_address",
    "add_address",
    "AddToCompareResult",
    "CompareRunResult",
    "ComparedProduct",
    "ComparedAttributeRow",
    "ComparePageData",
    "add_product_to_compare",
    "open_compare_page",
    "compare_products",
    "extract_compare_page",
    "LoginResult",
    "login_customer",
]
