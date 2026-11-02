#!/usr/bin/env python3
"""
Selector Validation Script - CONFIGURED FOR YOUR MAGENTO INSTANCE
Validates that all CSS selectors used in the API exist on your actual Magento instance.

This version is pre-configured with your Magento URL from constants.py

Usage:
    python selector_validator.py
"""

from playwright.sync_api import sync_playwright
import json
from datetime import datetime

# ============================================================================
# CONFIGURATION - AUTOMATICALLY LOADED FROM YOUR constants.py
# ============================================================================

# Your actual Magento instance from constants.py
MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"

# Optional: Add test credentials here to validate logged-in pages
# Leave as None to skip pages that require login
LOGIN_EMAIL = None  # Example: "test@example.com"
LOGIN_PASSWORD = None  # Example: "password123"

# UPDATE THESE: Product IDs that exist on your Magento instance
# You'll need to find real product IDs by browsing your store
TEST_SIMPLE_PRODUCT_ID = "1"  # Replace with actual simple product ID
TEST_CONFIGURABLE_PRODUCT_ID = "2"  # Replace with actual configurable product ID (if you have one)

# Map of all selectors used in your API, organized by page
SELECTORS_TO_VALIDATE = {
    "login_page": {
        "url": f"{MAGENTO_URL}/customer/account/login/",
        "requires_login": False,
        "selectors": [
            # From login.py
            "form#login-form",
            "input#email",
            "input#pass",
            "button#send2, button.action.login.primary",
            ".page.messages .message-error",
            ".page.messages .error.message",
            "div.messages .message-error",
            "div.messages .error.message",
        ]
    },
    
    "product_page": {
        "url": f"{MAGENTO_URL}/catalog/product/view/id/{TEST_SIMPLE_PRODUCT_ID}",
        "requires_login": False,
        "selectors": [
            # From product.py
            "form#product_addtocart_form",
            "input#qty",
            "h1.page-title span.base",
            "span.price-container span.price",
            ".product.attribute.sku .value",
            "#product-options-wrapper .fieldset > .field",
            ".stock.available span",
            ".stock span",
            "button#product-addtocart-button",
            ".product.media .gallery-placeholder img",
        ]
    },
    
    "cart_page": {
        "url": f"{MAGENTO_URL}/checkout/cart/",
        "requires_login": False,
        "selectors": [
            # From cart.py
            "form#form-validate",
            "table#shopping-cart-table tbody tr",
            ".product-item-name a",
            "input.qty",
            ".cart.item .price",
            ".cart-summary .grand.totals .price",
            "button.action.primary.checkout",
            ".action.action-delete",
        ]
    },
    
    "checkout_page": {
        "url": f"{MAGENTO_URL}/checkout/",
        "requires_login": False,
        "selectors": [
            # From shipping.py
            "li#shipping",
            "li#payment",
            "form#co-shipping-form",
            "form[data-role='email-with-possible-login'] input#customer-email",
            "input[name=firstname]",
            "input[name=lastname]",
            "input[name='street[0]']",
            "input[name=city]",
            "select[name=country_id]",
            "input[name=postcode]",
            "input[name=telephone]",
            "table.table-checkout-shipping-method tbody tr.row",
            "input[type=radio]",
            ".col-price .price",
            "form#co-shipping-method-form",
            "button[data-role='opc-continue']",
            "form#co-payment-form",
            "button.action.primary.checkout",
        ]
    },
    
    "account_page": {
        "url": f"{MAGENTO_URL}/customer/account/edit/",
        "requires_login": True,
        "selectors": [
            # From account.py
            "form#form-validate",
            "input#firstname",
            "input#lastname",
            "input#email",
            "input#change-email",
            "input#change-password",
            "input#current-password",
            "input#password",
            "input#password-confirmation",
            "button.action.save.primary",
        ]
    },
    
    "address_book_page": {
        "url": f"{MAGENTO_URL}/customer/address/",
        "requires_login": True,
        "selectors": [
            # From address_book.py
            ".block-addresses-list .item",
            "a.action.edit",
            "a.action.delete",
            "button.action.primary.add",
            "form#form-validate",
            "input[name=firstname]",
            "input[name=lastname]",
            "input[name='street[0]']",
            "input[name=city]",
            "select[name=country_id]",
            "input[name=postcode]",
            "input[name=telephone]",
            "input[name=default_billing]",
            "input[name=default_shipping]",
        ]
    },
    
    "order_history_page": {
        "url": f"{MAGENTO_URL}/sales/order/history/",
        "requires_login": True,
        "selectors": [
            # From order.py
            ".table-order-items tbody tr",
            ".col.id",
            ".col.date",
            ".col.total .price",
            ".col.status",
            ".col.actions .action.view",
            ".actions-toolbar .action.order",
        ]
    },
    
    "wishlist_page": {
        "url": f"{MAGENTO_URL}/wishlist/",
        "requires_login": True,
        "selectors": [
            # From wishlist.py
            "div.products-grid.wishlist ol.product-items > li",
            "strong.product-item-name a",
            "input[type='number'][name^='qty[']",
            "span.price",
            "img.product-image-photo",
            "a[data-role='remove']",
            "a.action.delete",
            "form.form-wishlist-items button.action.update",
        ]
    },
    
    "search_page": {
        "url": f"{MAGENTO_URL}/catalogsearch/result/?q=test",
        "requires_login": False,
        "selectors": [
            # From search.py
            "#search",
            ".products.list .item.product",
            ".product-item-link",
            ".price-box .price",
            "a.action.tocart.primary",
            ".pages-item-next",
        ]
    },
    
    "home_page": {
        "url": f"{MAGENTO_URL}/",
        "requires_login": False,
        "selectors": [
            # Basic Magento elements
            "#search",
            ".header.links",
            ".nav-sections",
        ]
    },
}

# ============================================================================
# VALIDATION LOGIC - NO NEED TO MODIFY BELOW
# ============================================================================

def login_if_needed(page, requires_login):
    """Login to Magento if credentials are provided and page requires it."""
    if not requires_login:
        return True
    
    if not LOGIN_EMAIL or not LOGIN_PASSWORD:
        print("    ⚠️  Page requires login but no credentials provided")
        print("       Set LOGIN_EMAIL and LOGIN_PASSWORD at top of script")
        return False
    
    try:
        # Go to login page
        page.goto(f"{MAGENTO_URL}/customer/account/login/", timeout=10000)
        page.wait_for_load_state("networkidle")
        
        # Fill login form
        page.fill("input#email", LOGIN_EMAIL)
        page.fill("input#pass", LOGIN_PASSWORD)
        page.click("button#send2, button.action.login.primary")
        page.wait_for_load_state("networkidle")
        
        # Check if login succeeded (not on login page anymore)
        if "customer/account/login" in page.url:
            print("    ❌ Login failed - check credentials")
            return False
        
        print("    ✅ Logged in successfully")
        return True
        
    except Exception as e:
        print(f"    ❌ Login error: {e}")
        return False


def validate_selectors():
    """
    Check that all selectors exist on actual Magento pages.
    Returns True if all selectors are valid, False otherwise.
    """
    results = {
        "timestamp": datetime.now().isoformat(),
        "magento_url": MAGENTO_URL,
        "summary": {
            "total_pages": 0,
            "total_selectors": 0,
            "passed": 0,
            "failed": 0,
            "warnings": 0,
            "skipped_pages": 0,
        },
        "pages": {}
    }
    
    print("="*70)
    print("MAGENTO SELECTOR VALIDATION")
    print("="*70)
    print(f"Target: {MAGENTO_URL}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        for page_name, config in SELECTORS_TO_VALIDATE.items():
            results["summary"]["total_pages"] += 1
            
            print(f"\n{'='*70}")
            print(f"Page: {page_name}")
            print(f"URL: {config['url']}")
            print(f"{'='*70}")
            
            page_results = {
                "url": config['url'],
                "requires_login": config['requires_login'],
                "selectors": {},
                "page_loaded": False,
                "login_required": config['requires_login'],
                "login_successful": False,
            }
            
            # Login if needed
            if config['requires_login']:
                if not login_if_needed(page, True):
                    print(f"  ⚠️  Skipping page - login required but failed")
                    results["summary"]["skipped_pages"] += 1
                    page_results["error"] = "Login required but failed"
                    results["pages"][page_name] = page_results
                    continue
                else:
                    page_results["login_successful"] = True
            
            # Load the page
            try:
                page.goto(config["url"], timeout=15000)
                page.wait_for_load_state("networkidle", timeout=10000)
                page_results["page_loaded"] = True
                
            except Exception as e:
                print(f"  ❌ Failed to load page: {e}")
                results["summary"]["warnings"] += 1
                page_results["error"] = str(e)
                results["pages"][page_name] = page_results
                continue
            
            # Validate each selector
            for selector in config["selectors"]:
                results["summary"]["total_selectors"] += 1
                
                try:
                    count = page.locator(selector).count()
                    
                    if count > 0:
                        print(f"  ✅ {selector} (found {count})")
                        results["summary"]["passed"] += 1
                        page_results["selectors"][selector] = {
                            "status": "found",
                            "count": count
                        }
                    else:
                        print(f"  ❌ {selector} (NOT FOUND)")
                        results["summary"]["failed"] += 1
                        page_results["selectors"][selector] = {
                            "status": "not_found",
                            "count": 0
                        }
                        
                except Exception as e:
                    print(f"  ⚠️  {selector} (ERROR: {e})")
                    results["summary"]["warnings"] += 1
                    page_results["selectors"][selector] = {
                        "status": "error",
                        "error": str(e)
                    }
            
            results["pages"][page_name] = page_results
        
        browser.close()
    
    # Print summary
    print(f"\n{'='*70}")
    print("VALIDATION SUMMARY")
    print(f"{'='*70}")
    print(f"Pages validated: {results['summary']['total_pages'] - results['summary']['skipped_pages']}/{results['summary']['total_pages']}")
    print(f"Selectors checked: {results['summary']['total_selectors']}")
    print(f"✅ Found: {results['summary']['passed']}")
    print(f"❌ Not found: {results['summary']['failed']}")
    print(f"⚠️  Errors: {results['summary']['warnings']}")
    
    if results['summary']['skipped_pages'] > 0:
        print(f"\n⚠️  {results['summary']['skipped_pages']} pages skipped (login required)")
        print("   Set LOGIN_EMAIL and LOGIN_PASSWORD at top of script to validate these")
    
    # Show failed selectors
    if results['summary']['failed'] > 0:
        print(f"\n{'='*70}")
        print("FAILED SELECTORS (Need to fix these!)")
        print(f"{'='*70}")
        for page_name, page_data in results['pages'].items():
            failed_selectors = [
                sel for sel, data in page_data.get('selectors', {}).items()
                if data['status'] == 'not_found'
            ]
            if failed_selectors:
                print(f"\n{page_name}:")
                for sel in failed_selectors:
                    print(f"  ❌ {sel}")
    
    # Save detailed results
    output_file = "selector_validation_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'='*70}")
    print(f"Detailed results saved to: {output_file}")
    print(f"{'='*70}")
    
    # Determine success
    all_selectors_valid = (
        results['summary']['failed'] == 0 and 
        results['summary']['warnings'] == 0
    )
    
    if all_selectors_valid:
        print("\n🎉 SUCCESS! All selectors are valid.")
        print("   Your API should work with this Magento instance!")
    else:
        print("\n⚠️  ISSUES FOUND!")
        print("   Fix the failed selectors before using the API in production.")
        print("\nNext steps:")
        print("  1. Browse your Magento store to find real product IDs")
        print("  2. Update TEST_SIMPLE_PRODUCT_ID at top of this script")
        print("  3. Optionally add LOGIN_EMAIL and LOGIN_PASSWORD for logged-in pages")
        print("  4. Re-run validation until all pass")
    
    return all_selectors_valid


if __name__ == "__main__":
    import sys
    success = validate_selectors()
    sys.exit(0 if success else 1)