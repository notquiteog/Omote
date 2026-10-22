# Shop and marketplace work

Open **Shop** in the sidebar. Choose a website and **Open with Omote**, or save a supplier or listing first. This prepares a request in chat; send it to start. Browser assistance needs no marketplace API credentials. Omote reads the live page and uses browser tools to research products, request quotes, fill listings, relist and compose replies. Login walls, challenges, account restrictions and changing layouts can require you to take over.

Sign in yourself in Omote’s browser when asked. Cookies and site storage stay in its persistent profile. Compatible chats share that account login but have separate tabs, page navigation and element references. Chat conversation histories remain separate. Closing one chat leaves the others open. After all browser chats close, the next starts with a blank tab and the saved profile. Expired or revoked site logins still require sign-in. A profile in use by an external process or incompatible display produces an actionable error instead of an unsigned temporary profile. Passwords and cookies are not exported to the model.

The lazy `commerce` tool saves project-scoped supplier/listing records. `catalog` gives schemas, `save` accepts a revision for updates, and `prepare` returns a quote inquiry or listing text, readiness checks and estimated profit. Use browser tools to carry out the prepared work. Orders, binding offers, listing fees and messages use the existing approval policy. Saving never sends, publishes or purchases. External statuses require observed evidence; live listings need their actual URL. Inspect the site before retrying an interrupted publication.

Supplier records cover Alibaba, Made-in-China, DHgate and other sites. Track landed cost, minimum order, samples, shipping, tracking, product/photo rights, returns and written wholesale terms. Profit estimates use your costs and fee rate; they do not fetch current fees or guarantee a margin.

Listing drafts cover eBay, Etsy, Depop, Facebook Marketplace and other sites. Basic checks are not an exhaustive policy review. Checked on 2026-10-22:

- [eBay](https://export.ebay.com/en/services-tools/shipping/shipping-tips-and-best-practice/drop-shipping/) permits owned inventory or appropriate wholesale agreements; buying from another retailer or marketplace after sale for direct delivery is not permitted.
- [Etsy](https://help.etsy.com/hc/en-us/articles/23948763872151-Does-Etsy-Allow-Drop-Shipping-or-Reselling) restricts ordinary reselling/dropshipping, with specific eligible categories and rules for original designs/production partners.
- [Depop](https://depophelp.zendesk.com/hc/en-gb/articles/22499808769553-Dropshipping-Policy) prohibits dropshipped catalog items; eligible seller-designed products have additional quality, original-photo and disclosure requirements.

## Automatic replies

The first unattended inbox connector is **eBay’s official Message API**. In Shop → Replies, expand **Connect eBay API (advanced)** and supply a user access token with `commerce.message` and `commerce.identity.readonly` scopes. Omote verifies identity and inbox access before saving the token in an owner-readable local file. Tokens are never returned in API responses or tool output. Token expiry pauses rules; automatic token refresh and a one-click OAuth flow are not implemented yet.

Enter an item ID, complete incoming question, exact answer, daily limit and expiry. Enabling explicitly authorizes that answer for new matching messages. Checks run once a minute while Omote runs; no LLM or generation service is used. The worker verifies recipient, item, active conversation and timestamp. Case, whitespace and final question/exclamation punctuation are normalized. Extra requests or attachments do not match. One answer is sent per conversation per rule, with up to 50 per rolling 24 hours and expiry within 30 days. Conflicting active rules are rejected.

An outgoing ledger shared across the user’s projects prevents duplicates across restarts and projects monitoring the same item. Each project sees only its own send history. The ledger is reserved before the request: an interrupted/failed send remains **uncertain**, is never blindly retried, and needs inspection on eBay. Network, token, rate-limit and API errors pause rules and show the problem. Pausing/disconnecting cannot retract a request already sent. Local-only mode prevents eBay network calls.

Other marketplaces currently use **assisted browser workflows**. Persistent login does not mean unattended replying is implemented for those sites. Scheduled browser inbox checks can use Tasks, but ordinary message approval still applies. Browser automation does not bypass site access controls or product restrictions.

API contract: [eBay Message API specification](https://developer.ebay.com/api-docs/master/commerce/message/openapi/3/commerce_message_v1_oas3.json).

## Verification

`tests/test_commerce.py` checks storage, project/user boundaries, eligibility, exact replies, stale messages, duplicates across restarts/projects, caps, uncertain outcomes, token redaction, wire payloads and local-only mode. `tests/test_commerce_browser.py` exercises desktop/mobile drafts, browser handoff and explicit enable/pause. `tests/test_browser_persistence.py` uses intercepted test pages and a synthetic cookie to verify login retention and separate tabs. Tests contact no real sellers, publish no real listings, and do not prove live Facebook/eBay account flows.
