"""
Test Configuration File
Centralized configuration for all validation and integration tests.

This file pulls values from your constants.py and config.py files.
Edit the values below to match your test environment.
"""

# ============================================================================
# MAGENTO INSTANCE - Automatically from your constants.py
# ============================================================================

# Your actual Magento instance URL
MAGENTO_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082"

# ============================================================================
# TEST CREDENTIALS - From your config.py
# ============================================================================

# Option 1: Use the default customer from config.py (if you have it configured)
try:
    from config import DEFAULT_CUSTOMER_EMAIL, DEFAULT_CUSTOMER_PASSWORD
    TEST_EMAIL = DEFAULT_CUSTOMER_EMAIL
    TEST_PASSWORD = DEFAULT_CUSTOMER_PASSWORD
    print(f"✅ Using credentials from config.py: {TEST_EMAIL}")
except ImportError:
    # Option 2: Set manually here
    TEST_EMAIL = None  # "your-test-user@example.com"
    TEST_PASSWORD = None  # "YourPassword123"
    print("⚠️  No credentials in config.py - set TEST_EMAIL and TEST_PASSWORD in test_config.py")

# ============================================================================
# TEST PRODUCTS - You need to find these on your Magento instance
# ============================================================================

# Using the V8 Energy drink product you found
TEST_SIMPLE_PRODUCT_URL = "http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082/v8-energy-healthy-energy-drink-steady-energy-from-black-and-green-tea-pomegranate-blueberry-8-ounce-can-pack-of-24.html"

# Optional: For testing products with options (size, color, etc.)
# Find a configurable product if you need to test that functionality
TEST_CONFIGURABLE_PRODUCT_URL = None  # Update if you have a configurable product

# ============================================================================
# TEST BEHAVIOR
# ============================================================================

# Show browser during tests (useful for debugging)
HEADLESS = True  # Set to False to see browser

# Slow down browser actions (milliseconds, useful for watching tests)
SLOW_MO = 0  # Set to 100-500 to slow down and watch

# Timeouts
PAGE_LOAD_TIMEOUT = 15000  # milliseconds
NETWORK_IDLE_TIMEOUT = 10000  # milliseconds

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_test_config():
    """Get all test configuration as a dictionary."""
    return {
        "magento_url": MAGENTO_URL,
        "test_email": TEST_EMAIL,
        "test_password": TEST_PASSWORD,
        "test_product_url": TEST_SIMPLE_PRODUCT_URL,
        "configurable_product_url": TEST_CONFIGURABLE_PRODUCT_URL,
        "headless": HEADLESS,
        "slow_mo": SLOW_MO,
        "page_load_timeout": PAGE_LOAD_TIMEOUT,
        "network_idle_timeout": NETWORK_IDLE_TIMEOUT,
    }


def validate_config():
    """Check if configuration is complete."""
    issues = []
    warnings = []
    
    if not TEST_EMAIL or not TEST_PASSWORD:
        issues.append("❌ TEST_EMAIL and TEST_PASSWORD not set")
        issues.append("   Your config.py has placeholders: customer@example.com / secret")
        issues.append("   You need to either:")
        issues.append("   1. Create a real customer account on your Magento")
        issues.append("   2. Update config.py with real credentials")
        issues.append("   3. Or set TEST_EMAIL/TEST_PASSWORD directly in test_config.py")
    
    if not TEST_SIMPLE_PRODUCT_URL or "catalog/product/view/id/1" in TEST_SIMPLE_PRODUCT_URL:
        warnings.append("⚠️  Using default product URL - update if needed")
    
    if issues:
        print("\n" + "="*70)
        print("CONFIGURATION ISSUES - CANNOT RUN TESTS")
        print("="*70)
        for issue in issues:
            print(issue)
        print("\n" + "="*70 + "\n")
        return False
    else:
        print("\n✅ Configuration looks good!")
        print(f"   Magento: {MAGENTO_URL}")
        print(f"   Test user: {TEST_EMAIL}")
        print(f"   Product: {TEST_SIMPLE_PRODUCT_URL}")
        
        if warnings:
            print("\nWarnings:")
            for warning in warnings:
                print(f"   {warning}")
        
        print()
        return True


if __name__ == "__main__":
    # Run validation when file is executed
    is_valid = validate_config()
    
    # Print config
    print("\nCurrent Configuration:")
    print("="*70)
    config = get_test_config()
    for key, value in config.items():
        print(f"{key:25} = {value}")
    print("="*70)
    
    if not is_valid:
        print("\n" + "="*70)
        print("NEXT STEPS:")
        print("="*70)
        print("1. Go to your Magento: http://ec2-18-218-205-96.us-east-2.compute.amazonaws.com:8082")
        print("2. Create a customer account (or use existing)")
        print("3. Update config.py lines 8-9:")
        print('   DEFAULT_CUSTOMER_EMAIL = "your-real-email@example.com"')
        print('   DEFAULT_CUSTOMER_PASSWORD = "your-real-password"')
        print("4. Re-run: python test_config.py")
        print("="*70)