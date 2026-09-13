"""Declarative specification of the BlindSpot sample dataset.

The dataset is *designed*, not random: each incident is written against a known
test corpus so its expected verdict is a fact about the data rather than a guess.
That is what makes `scripts/evaluate.py` a real measurement.

Three groups of incidents:

  COVERED      the production condition is exercised by a test at that value
  PARTIAL      the input is tested, but never at the production value
  NOT_COVERED  the behaviour is not exercised by any test, or only in pieces

Recurring families are seeded deliberately so the Blind Spots screen has
something true to show: boundary conditions, null/empty inputs, permissions,
retry/timeout, unicode and error handling all repeat across features.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FEATURES = (
    "Authentication",
    "Checkout",
    "Payments",
    "Orders",
    "Profile",
    "Search",
    "Notifications",
)


@dataclass(frozen=True)
class TestSpec:
    """One test in the synthetic corpus."""

    key: str
    feature: str
    scenario: str
    expected: str
    inputs: dict[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    #: pytest-only: emit as @pytest.mark.parametrize over this input
    parametrize: tuple[str, tuple[str, ...]] | None = None


@dataclass(frozen=True)
class IncidentSpec:
    """One production incident plus the verdict the data justifies."""

    key: str
    feature: str
    text: str
    expected_coverage: str
    expected_family: str
    severity: str = "HIGH"
    note: str = ""


# ---------------------------------------------------------------------------
# The test corpus: what the organisation *does* test.
# ---------------------------------------------------------------------------

CORE_TESTS: tuple[TestSpec, ...] = (
    # -- Checkout: discounts are tested at 10% and 20% only ------------------
    TestSpec("checkout_no_coupon", "Checkout", "Checkout without a coupon", "Order succeeds"),
    TestSpec("checkout_discount_10", "Checkout", "Checkout with a 10% discount coupon",
             "Order total is reduced by 10%", {"discount": "10%"}),
    TestSpec("checkout_discount_20", "Checkout", "Checkout with a 20% discount coupon",
             "Order total is reduced by 20%", {"discount": "20%"}),
    TestSpec("checkout_expired_coupon", "Checkout", "Checkout with an expired coupon",
             "Coupon is rejected", {"coupon": "expired"}),
    TestSpec("checkout_shipping_standard", "Checkout", "Checkout with standard shipping",
             "Shipping cost is added", {"shipping": "standard"}),
    TestSpec("checkout_tax_applied", "Checkout", "Checkout applies tax to the order total",
             "Tax appears on the order", {"tax": "8"}),
    TestSpec("checkout_quantity_single", "Checkout", "Checkout with a single item",
             "Order succeeds", {"quantity": "1"}),
    TestSpec("checkout_quantity_many", "Checkout", "Checkout with ten items",
             "Order succeeds", {"quantity": "10"}),

    # -- Payments: timeout and retry exist, but never together ---------------
    TestSpec("payment_card_success", "Payments", "Payment with a valid card succeeds",
             "Payment is captured", {"card": "valid"}),
    TestSpec("payment_declined", "Payments", "Payment with a declined card",
             "Decline is surfaced to the user", {"card": "declined"}),
    TestSpec("payment_gateway_timeout", "Payments", "Payment gateway timeout is handled",
             "Payment is marked pending"),
    TestSpec("payment_retry_after_failure", "Payments", "Payment retry after a failed attempt",
             "Second attempt succeeds"),
    TestSpec("payment_refund_full", "Payments", "Full refund of a captured payment",
             "Refund is issued", {"amount": "100"}),
    TestSpec("payment_refund_partial", "Payments", "Partial refund of a captured payment",
             "Partial refund is issued", {"amount": "50"}),

    # -- Authentication ------------------------------------------------------
    TestSpec("login_valid", "Authentication", "Login with valid credentials",
             "Session is created", {"password": "valid"}),
    TestSpec("login_wrong_password", "Authentication", "Login with an incorrect password",
             "Login is rejected", {"password": "invalid"}),
    TestSpec("login_unknown_user", "Authentication", "Login with an unknown username",
             "Login is rejected", {"username": "unknown"}),
    TestSpec("logout_clears_session", "Authentication", "Logout clears the active session",
             "Session is destroyed"),
    TestSpec("password_reset_email", "Authentication", "Password reset sends an email",
             "Reset email is sent"),
    TestSpec("session_expiry", "Authentication", "Expired session is rejected",
             "User is redirected to login"),

    # -- Orders --------------------------------------------------------------
    TestSpec("order_create", "Orders", "Creating an order from a cart", "Order is created"),
    TestSpec("order_cancel_pending", "Orders", "Cancelling a pending order",
             "Order status becomes cancelled", {"status": "pending"}),
    TestSpec("order_track", "Orders", "Tracking a shipped order", "Tracking details are returned"),
    TestSpec("order_list_pagination", "Orders", "Listing orders with pagination",
             "Second page is returned", {"page": "2"}),

    # -- Profile: ASCII only -------------------------------------------------
    TestSpec("profile_update_name", "Profile", "Updating the display name with an ASCII value",
             "Profile is saved", {"name": "John Smith"}),
    TestSpec("profile_update_address", "Profile", "Updating the shipping address",
             "Address is saved"),
    TestSpec("profile_change_email", "Profile", "Changing the account email address",
             "Email is updated and verified"),

    # -- Search --------------------------------------------------------------
    TestSpec("search_basic", "Search", "Searching for a product by name", "Results are returned"),
    TestSpec("search_no_results", "Search", "Searching for a term with no matches",
             "An empty result set is returned"),
    TestSpec("search_filter_category", "Search", "Filtering search results by category",
             "Only matching products are returned"),
    TestSpec("search_sort_price", "Search", "Sorting search results by price",
             "Results are ordered by price"),
    TestSpec("search_page_size_20", "Search", "Search results with a page size of 20",
             "Twenty results are returned", {"page_size": "20"}),

    # -- Notifications -------------------------------------------------------
    TestSpec("notify_order_email", "Notifications", "Order confirmation email is sent",
             "Email is queued"),
    TestSpec("notify_unsubscribe", "Notifications", "Unsubscribing stops marketing email",
             "User is unsubscribed"),
    TestSpec("notify_push_delivery", "Notifications", "Push notification delivery",
             "Notification is delivered"),
)

#: Tests that use pytest parametrisation, proving the parser reads the values.
PARAMETRIZED_TESTS: tuple[TestSpec, ...] = (
    TestSpec(
        "checkout_tax_rates", "Checkout", "Checkout across supported tax rates",
        "Tax is calculated correctly", {},
        parametrize=("tax", ("0", "5", "8", "12")),
    ),
    TestSpec(
        "search_page_sizes", "Search", "Search honours the supported page sizes",
        "Requested number of results is returned", {},
        parametrize=("page_size", ("10", "20", "50")),
    ),
)


# ---------------------------------------------------------------------------
# The incidents: what production actually did.
# ---------------------------------------------------------------------------

INCIDENTS: tuple[IncidentSpec, ...] = (
    # ===== BOUNDARY CONDITIONS (recurring family, 7 incidents) =============
    IncidentSpec(
        "boundary_discount_100", "Checkout",
        "Checkout failed when a customer applied a 100% discount coupon during a promotion.\n"
        "Root cause: division by zero in the discount calculation when the payable total reached zero.",
        "PARTIAL", "BOUNDARY_CONDITIONS", "CRITICAL",
        "discount is tested at 10% and 20% but never at 100%",
    ),
    IncidentSpec(
        "boundary_quantity_zero", "Checkout",
        "Order rejected with a generic 500 when the cart quantity was 0 after an item was removed in another tab.\n"
        "Root cause: quantity of 0 was not validated before the total was computed.",
        "PARTIAL", "BOUNDARY_CONDITIONS", "HIGH",
        "quantity is tested at 1 and 10, never at 0",
    ),
    IncidentSpec(
        "boundary_refund_over", "Payments",
        "Refund failed when the requested refund amount of 150 exceeded the captured amount of 100.\n"
        "Root cause: no upper bound check on the refund amount.",
        "PARTIAL", "BOUNDARY_CONDITIONS", "HIGH",
        "refund amount tested at 50 and 100, never above the captured total",
    ),
    IncidentSpec(
        "boundary_page_size_max", "Search",
        "Search timed out when a client requested a page size of 500 results.\n"
        "Root cause: no maximum page size was enforced.",
        "PARTIAL", "BOUNDARY_CONDITIONS", "MEDIUM",
        "page_size tested at 10/20/50, never at 500",
    ),
    IncidentSpec(
        "boundary_tax_negative", "Checkout",
        "Checkout produced a negative order total when a tax rate of -5 was configured for a region.",
        "PARTIAL", "BOUNDARY_CONDITIONS", "HIGH",
        "tax tested at 0/5/8/12, never negative",
    ),
    IncidentSpec(
        "boundary_discount_negative", "Checkout",
        "A negative discount of -10% increased the order total instead of being rejected.",
        "PARTIAL", "BOUNDARY_CONDITIONS", "HIGH",
        "discount never tested with a negative value",
    ),
    IncidentSpec(
        "boundary_order_page_max", "Orders",
        "Listing orders with page 10000 returned a 500 instead of an empty page.\n"
        "Root cause: offset overflow when the page number exceeded the result count.",
        "PARTIAL", "BOUNDARY_CONDITIONS", "MEDIUM",
        "page tested at 2 only",
    ),

    # ===== NULL / EMPTY INPUTS (recurring family, 6 incidents) =============
    IncidentSpec(
        "null_email_profile", "Profile",
        "Profile update crashed when the email field was null after a third-party sync.\n"
        "Root cause: null email was dereferenced without a check.",
        "PARTIAL", "NULL_EMPTY_INPUTS", "HIGH",
        "the email input is exercised, but only with a valid address",
    ),
    IncidentSpec(
        "empty_cart_checkout", "Checkout",
        "Checkout returned an unhandled exception when the cart was empty.",
        "NOT_COVERED", "NULL_EMPTY_INPUTS", "HIGH",
    ),
    IncidentSpec(
        "missing_address_order", "Orders",
        "Order creation failed when the shipping address field was missing from the request payload.",
        "NOT_COVERED", "NULL_EMPTY_INPUTS", "HIGH",
    ),
    IncidentSpec(
        "null_name_profile", "Profile",
        "Saving a profile with a null display name wrote an empty record instead of rejecting the request.",
        "PARTIAL", "NULL_EMPTY_INPUTS", "MEDIUM",
        "the display name is tested, but only with an ASCII value",
    ),
    IncidentSpec(
        "empty_query_search", "Search",
        "An empty search query returned the entire catalogue and overloaded the database.",
        "NOT_COVERED", "NULL_EMPTY_INPUTS", "HIGH",
    ),
    IncidentSpec(
        "missing_token_notification", "Notifications",
        "Push delivery failed when the device token field was absent for users who had reinstalled the app.",
        "NOT_COVERED", "NULL_EMPTY_INPUTS", "MEDIUM",
    ),

    # ===== PERMISSION COMBINATIONS (recurring family, 4 incidents) =========
    IncidentSpec(
        "permission_order_view", "Orders",
        "A customer support agent with a read-only role was able to cancel another customer's order.\n"
        "Root cause: the permission check verified authentication but not the role.",
        "NOT_COVERED", "PERMISSION_COMBINATIONS", "CRITICAL",
    ),
    IncidentSpec(
        "permission_refund_issue", "Payments",
        "A junior agent role could issue refunds above the approval limit. Authorisation was not enforced per role.",
        "NOT_COVERED", "PERMISSION_COMBINATIONS", "CRITICAL",
    ),
    IncidentSpec(
        "permission_profile_edit", "Profile",
        "One user could edit another user's profile because the ownership check was missing; the endpoint returned 200 instead of 403.",
        "NOT_COVERED", "PERMISSION_COMBINATIONS", "CRITICAL",
    ),
    IncidentSpec(
        "permission_search_internal", "Search",
        "Unauthorised users could see internal-only products in search results because the role filter was not applied.",
        "NOT_COVERED", "PERMISSION_COMBINATIONS", "HIGH",
    ),

    # ===== RETRY / TIMEOUT (recurring family, 5 incidents) =================
    IncidentSpec(
        "retry_payment_timeout", "Payments",
        "Payment failed when an immediate retry occurred after a gateway timeout, and the customer was charged twice.\n"
        "Root cause: the retry did not check whether the original request had already settled.",
        "NOT_COVERED", "STATE_TRANSITIONS", "CRITICAL",
        "timeout and retry are each tested, never in combination — spec §56 calls "
        "this a state/sequence combination gap",
    ),
    IncidentSpec(
        "retry_notification_storm", "Notifications",
        "A downstream timeout caused notification retries to loop without backoff and flood users with duplicate emails.",
        "NOT_COVERED", "RESILIENCE_RETRY_TIMEOUT", "HIGH",
    ),
    IncidentSpec(
        "timeout_search_backend", "Search",
        "Search requests hung indefinitely when the search backend stopped responding; no timeout was applied.",
        "NOT_COVERED", "RESILIENCE_RETRY_TIMEOUT", "HIGH",
    ),
    IncidentSpec(
        "retry_order_submit", "Orders",
        "Submitting an order twice after a timeout created two identical orders for the same cart.",
        "NOT_COVERED", "RESILIENCE_RETRY_TIMEOUT", "HIGH",
    ),
    IncidentSpec(
        "timeout_login_provider", "Authentication",
        "Login failed with a blank page when the identity provider timed out instead of showing an error.",
        "NOT_COVERED", "RESILIENCE_RETRY_TIMEOUT", "MEDIUM",
    ),

    # ===== UNICODE / DATA FORMAT (recurring family, 4 incidents) ===========
    IncidentSpec(
        "unicode_profile_name", "Profile",
        "User profile update fails when the display name contains unicode characters such as emoji or accents.\n"
        "Root cause: the column encoding could not store four-byte UTF-8 characters.",
        "NOT_COVERED", "INVALID_INPUT_VALIDATION", "MEDIUM",
    ),
    IncidentSpec(
        "unicode_search_query", "Search",
        "Searching with non-ASCII characters returned no results even when matching products existed.",
        "NOT_COVERED", "INVALID_INPUT_VALIDATION", "MEDIUM",
    ),
    IncidentSpec(
        "invalid_email_format", "Profile",
        "A malformed email address was accepted and stored, causing every later notification to bounce.",
        "NOT_COVERED", "INVALID_INPUT_VALIDATION", "MEDIUM",
    ),
    IncidentSpec(
        "unicode_notification_subject", "Notifications",
        "Emails with emoji in the subject line were delivered with a corrupted subject.",
        "NOT_COVERED", "INVALID_INPUT_VALIDATION", "LOW",
    ),

    # ===== CONCURRENCY (3 incidents) ======================================
    IncidentSpec(
        "concurrency_double_checkout", "Checkout",
        "Two concurrent checkout requests for the same cart created duplicate orders.\n"
        "Root cause: a race condition with no lock on the cart.",
        "NOT_COVERED", "CONCURRENCY", "CRITICAL",
    ),
    IncidentSpec(
        "concurrency_inventory", "Orders",
        "Simultaneous orders for the last item in stock both succeeded and oversold the product.",
        "NOT_COVERED", "CONCURRENCY", "CRITICAL",
    ),
    IncidentSpec(
        "concurrency_session_refresh", "Authentication",
        "Two parallel session refresh requests invalidated each other and logged the user out.",
        "NOT_COVERED", "CONCURRENCY", "HIGH",
    ),

    # ===== ERROR HANDLING (3 incidents) ===================================
    IncidentSpec(
        "error_login_500", "Authentication",
        "Login with an invalid password returned a 500 error instead of a rejection message.\n"
        "Root cause: an unhandled exception in the credential comparison path.",
        "PARTIAL", "ERROR_HANDLING", "HIGH",
        "the invalid-password scenario is tested; the error path is not",
    ),
    IncidentSpec(
        "error_payment_gateway_500", "Payments",
        "A gateway error surfaced as an unhandled exception and a stack trace was shown to the customer.",
        "NOT_COVERED", "ERROR_HANDLING", "HIGH",
        "no Payments test exercises an error path at all, so the dimension is untested",
    ),
    IncidentSpec(
        "error_order_cancel_shipped", "Orders",
        "Cancelling an already-shipped order threw an unhandled exception instead of returning a clear error.",
        "NOT_COVERED", "ERROR_HANDLING", "MEDIUM",
        "only the pending-order cancel path is tested; the error path is absent",
    ),

    # ===== COVERED / INEFFECTIVE (4 incidents) ============================
    IncidentSpec(
        "covered_discount_10_total", "Checkout",
        "Checkout with a 10% discount coupon produced the wrong order total for orders containing a gift card.",
        "COVERED", "TEST_QUALITY", "HIGH",
        "discount = 10% is directly tested, so the test itself is ineffective",
    ),
    IncidentSpec(
        "covered_declined_card", "Payments",
        "A declined card produced a confusing message; the decline itself was handled as designed.",
        "COVERED", "TEST_QUALITY", "MEDIUM",
        "card = declined is directly tested",
    ),
    IncidentSpec(
        "covered_expired_coupon", "Checkout",
        "An expired coupon was rejected but the order total still showed the discounted price on the summary page.",
        "COVERED", "TEST_QUALITY", "MEDIUM",
        "coupon = expired is directly tested",
    ),
    IncidentSpec(
        "covered_login_unknown_user", "Authentication",
        "Login with an unknown username was rejected, but the response time revealed whether the account existed.",
        "COVERED", "TEST_QUALITY", "MEDIUM",
        "username = unknown is directly tested",
    ),

    # ===== STATE TRANSITIONS (3 incidents) ================================
    IncidentSpec(
        "state_cancel_after_ship", "Orders",
        "An order moved from shipped back to pending after a delayed webhook arrived out of order.",
        "NOT_COVERED", "STATE_TRANSITIONS", "HIGH",
    ),
    IncidentSpec(
        "state_refund_before_capture", "Payments",
        "A refund was attempted before the payment was captured and left the transaction in an inconsistent state.",
        "NOT_COVERED", "STATE_TRANSITIONS", "HIGH",
    ),
    IncidentSpec(
        "state_checkout_after_expiry", "Checkout",
        "Completing checkout after the cart had already expired produced an order with stale prices.",
        "NOT_COVERED", "STATE_TRANSITIONS", "MEDIUM",
    ),

    # ===== ENVIRONMENT (2 incidents) ======================================
    IncidentSpec(
        "environment_timezone_order", "Orders",
        "Order timestamps were a day off for customers in the Asia/Tokyo timezone; only production uses UTC storage.",
        "NOT_COVERED", "ENVIRONMENT", "MEDIUM",
    ),
    IncidentSpec(
        "environment_locale_currency", "Checkout",
        "Prices displayed with the wrong decimal separator for the German locale in the production environment.",
        "NOT_COVERED", "ENVIRONMENT", "LOW",
    ),

    # ===== Second wave: the same families recurring across more features ===
    # These are what turn a set of one-off findings into a measurable pattern.
    IncidentSpec(
        "boundary_password_length", "Authentication",
        "Sign-up accepted a password of 0 characters when the client-side check was bypassed.",
        "PARTIAL", "TEST_QUALITY", "HIGH",
        "the password input is tested, but never at a length boundary",
    ),
    IncidentSpec(
        "boundary_notification_batch", "Notifications",
        "A digest containing 10000 notifications exceeded the maximum payload size and was silently dropped.",
        "NOT_COVERED", "BOUNDARY_CONDITIONS", "MEDIUM",
    ),
    IncidentSpec(
        "null_payment_currency", "Payments",
        "Payment capture failed when the currency field was null for orders created by the legacy importer.",
        "NOT_COVERED", "NULL_EMPTY_INPUTS", "HIGH",
    ),
    IncidentSpec(
        "empty_password_login", "Authentication",
        "Submitting an empty password field produced an unhandled exception rather than a validation error.",
        "PARTIAL", "NULL_EMPTY_INPUTS", "HIGH",
        "password is tested as valid and invalid, never as empty",
    ),
    IncidentSpec(
        "missing_quantity_checkout", "Checkout",
        "Checkout failed when the quantity field was missing from the request body.",
        "PARTIAL", "NULL_EMPTY_INPUTS", "MEDIUM",
        "quantity is tested at 1 and 10, never omitted",
    ),
    IncidentSpec(
        "permission_notification_broadcast", "Notifications",
        "A marketing role could broadcast a notification to all users without the required approval scope.",
        "NOT_COVERED", "PERMISSION_COMBINATIONS", "HIGH",
    ),
    IncidentSpec(
        "retry_refund_duplicate", "Payments",
        "A refund retried after a timeout issued the refund twice to the customer.",
        "NOT_COVERED", "STATE_TRANSITIONS", "CRITICAL",
        "retry and timeout are tested separately; the combination is the gap",
    ),
    IncidentSpec(
        "timeout_profile_sync", "Profile",
        "Profile sync hung when the downstream identity service timed out, blocking the request thread.",
        "NOT_COVERED", "RESILIENCE_RETRY_TIMEOUT", "MEDIUM",
    ),
    IncidentSpec(
        "unicode_address_field", "Profile",
        "Shipping labels were corrupted for addresses containing Cyrillic characters.",
        "NOT_COVERED", "INVALID_INPUT_VALIDATION", "MEDIUM",
    ),
    IncidentSpec(
        "invalid_date_format_order", "Orders",
        "An order filter with a malformed date format returned a 500 instead of a validation error.",
        "NOT_COVERED", "INVALID_INPUT_VALIDATION", "MEDIUM",
    ),
    IncidentSpec(
        "concurrency_coupon_redeem", "Checkout",
        "A single-use coupon was redeemed twice by two simultaneous checkout requests.",
        "NOT_COVERED", "CONCURRENCY", "HIGH",
    ),
    IncidentSpec(
        "error_search_backend_exception", "Search",
        "A backend exception during search surfaced as a raw stack trace in the API response.",
        "NOT_COVERED", "ERROR_HANDLING", "MEDIUM",
    ),
    IncidentSpec(
        "state_notification_after_unsubscribe", "Notifications",
        "Marketing email continued to be sent after a user unsubscribed because the queued job was not cancelled.",
        "NOT_COVERED", "STATE_TRANSITIONS", "MEDIUM",
    ),
    IncidentSpec(
        "covered_logout_session", "Authentication",
        "Logout cleared the session but the browser back button still rendered cached account data.",
        "COVERED", "TEST_QUALITY", "MEDIUM",
        "the logout-clears-session scenario is directly tested",
    ),
    IncidentSpec(
        "environment_feature_flag", "Search",
        "A search ranking feature flag was enabled only in production and changed result ordering unexpectedly.",
        "NOT_COVERED", "ENVIRONMENT", "LOW",
    ),
)


#: Filler scenario templates used to pad the corpus to a realistic size without
#: accidentally covering any of the incidents above.
FILLER_SCENARIOS: dict[str, tuple[str, ...]] = {
    "Authentication": (
        "Login form renders the {noun} field",
        "Remember-me keeps the {noun} across a browser restart",
        "Account lockout after repeated attempts shows the {noun} message",
        "Sign-up validates the {noun} field",
        "Login audit log records the {noun}",
    ),
    "Checkout": (
        "Cart summary displays the {noun} line",
        "Checkout step navigation preserves the {noun}",
        "Gift wrapping option updates the {noun}",
        "Saved cards appear in the {noun} step",
        "Order review page shows the {noun}",
    ),
    "Payments": (
        "Payment form validates the {noun} field",
        "Receipt includes the {noun}",
        "Payment history lists the {noun}",
        "Saved card list shows the {noun}",
        "Currency selector updates the {noun}",
    ),
    "Orders": (
        "Order detail page shows the {noun}",
        "Order history filters by {noun}",
        "Invoice download includes the {noun}",
        "Delivery estimate displays the {noun}",
        "Order notes accept a {noun}",
    ),
    "Profile": (
        "Profile page displays the {noun}",
        "Avatar upload updates the {noun}",
        "Notification preferences save the {noun}",
        "Linked accounts list shows the {noun}",
        "Account deletion confirms the {noun}",
    ),
    "Search": (
        "Search suggestions include the {noun}",
        "Recent searches store the {noun}",
        "Search result card shows the {noun}",
        "Category browse page lists the {noun}",
        "Search analytics record the {noun}",
    ),
    "Notifications": (
        "Notification centre lists the {noun}",
        "Email template renders the {noun}",
        "Digest schedule includes the {noun}",
        "Notification preferences show the {noun}",
        "Delivery report records the {noun}",
    ),
}

FILLER_NOUNS: tuple[str, ...] = (
    "summary", "label", "status", "title", "badge", "timestamp", "identifier",
    "description", "total", "reference", "icon", "tooltip", "heading", "footer",
    "banner", "counter", "breadcrumb", "avatar", "subtitle", "tag",
)
