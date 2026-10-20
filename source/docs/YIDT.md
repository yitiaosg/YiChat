# YIDT wallet service

YIDT replaces spendable points. The authoritative ledger is in `userdata.db`; the old `points_accounts` and `points_ledger` tables remain as migration archives. Amounts are integer multiples of 10^-16, serialized as decimal TEXT in SQLite to avoid the signed 64-bit limit and JSON floating-point loss. YCards product prices retain their existing cent denominations; wallet transfers and Parachute stakes accept 16 decimal places. Parachute gross redemption is floored to 0.01 before gas.

## Supply and fees

The fixed supply is 4,294,967,296 YIDT. The genesis entry issues it to `yidt-000000000000`; one-time migration allocations move existing balances at 1 point = 1 YIDT without migration fees. Existing users without an old points row receive a zero-balance wallet. New users receive no wallet or grant automatically.

Every new wallet transfer charges 0.00390625 YIDT, collected by the official wallet. User payments debit requested amount plus gas. If available balance covers the requested amount but not its gas, the entire balance is debited and the recipient receives balance minus gas. Official payouts debit the gross amount and credit the recipient gross minus gas (gas stays in the treasury).

Service purchase amount and existing product fees are combined into a single transfer per order; batch publication is one combined transfer. Auctions transfer their nominal reserve to the official wallet. A leading/outbid/refunded reserve is represented in `yidt_escrow`, counted in the source wallet's assets. Settlement pays from the treasury and refunds only unused reserve; it never charges the bidder again. Existing order fees remain. Where all-in gas reduces treasury credit, actual credited atomic units determine escrow assets and refunds; existing cent-denominated bid/fee quotes remain nominal and the shortfall reduces treasury fee income. Official payouts cannot consume funds still recorded as auction escrow. A pending bid cannot switch its source wallet while its existing escrow remains outstanding.

Parachute records payment in `yidt_parachute_pending`. The player must explicitly click Start; only then does server flight timing begin. Payout is routed to the original paying wallet, even if the service binding changes. The minimum entered stake is 10 YIDT; the previous all-in product-fee rule can reduce the effective stake, and gas can further reduce it.

## Identity and authorization

YIDT is a dedicated OIDC public client using authorization code + S256 PKCE, state, nonce, issuer/audience/signature validation. Guest accounts are enabled for this client only. The provider adds a browser-session `sid`. First-party apps share the existing host-only main-site session, whereas YIDT has its own host-only cookie and signing key. Every authenticated wallet request checks the main browser sid. Logging out on the main site revokes only that sid, including its linked OIDC grants. Account-wide security revocations invalidate all account sids. There is no YIDT logout UI.

Service binding is manual, one current wallet per account/service. Bindings survive removing a wallet from an account's imported-wallet list. Multiple accounts can import and control the same wallet. Names are per-account, limited to 20 Unicode characters. Guest has 1 imported wallet slot; user/admin have 16. Imported existing wallets count toward the limit. The official treasury has no user-importable secret.

Payments require a short-lived intent tied to user, browser sid, service, exact request payload, amount, address, and binding version. The YIDT confirmation page approves it; the returning service request consumes it in the same SQLite transaction as the business mutation and transfer. Browser replay uses sessionStorage and an intent header. Binding/lock changes invalidate the intent. A six-digit account payment PIN is required only for locked services/direct transfers. PIN setup/reset uses existing main-site second factors, or fresh password/Google identity authentication where no sensitive second factor is configured. Wrong PIN/password attempts are rate-limited; PINs are password-hashed. PIN settings live only at `https://yidt.yitiao.org/pin/`; `/account/` does not show a PIN management entry. Main-site account routes handle identity verification and redirect back to the wallet-domain form. Verification flows in `yidt_account_flows` bind user, browser sid, selected method and expiration; switching methods preserves the flow. Successful verification grants five minutes to save, and saving consumes the grant atomically. Google failures return to the same verification flow. The wallet header uses the main site’s shared avatar file and username; the public explorer remains anonymous.

Wallet backup uses BIP39 English 12-word phrases. A 32-byte private key is derived from the BIP39 seed with the YIDT v1 domain separator. Both import methods recover existing wallets. Secret material is Fernet-encrypted with `.yidt_key`, mode 0600. Never lose this key. Backup view requires a PIN, expires after 60 seconds, hides when backgrounded, disables caching, and checks a random word or final key characters. Websites cannot guarantee blocking screenshots or external cameras.

## Public explorer

`/explorer/`, `/explorer/tx/<hash>`, `/explorer/address/<address>` are public. The explorer landing page lists only transactions involving the signed-in account’s currently imported wallets; signed-out/revoked sessions see no recent transactions. Full-hash/address searches and detail pages remain public. They expose addresses, balances, UTC times, amounts, gas, current hash and preceding hash, but omit account names, wallet names and service bindings. Service transaction descriptions are notes. Notes are visible to the wallet controller in their recent list and transaction detail, and remain hidden from anonymous/other viewers. Hash bodies include private metadata; full audit runs server-side. Search accepts only a complete 64-character lowercase hex hash or full wallet address (16 lowercase letters/digits after `yidt-`, with the official address as the sole shorter exception); it never uses fuzzy matching. This is a centralized hash-chained ledger, not an external blockchain.

Sealed-auction service responses omit explorer links. Public on-ledger amounts can still be correlated, as accepted in the specification. Other service payment receipts provide explorer links. Daily claims appear in `/account/`, including the current GMT day's receipt; streak bonuses remain 200 extra every fifth consecutive day.

Daily asset deltas belong to wallets. Dashboard totals add current imported wallets' deltas including the entire day before import, and include escrow. A zero opening balance displays no percentage.

## Deployment / audit / recovery

- `yidt.service`: Gunicorn, localhost port 5010; startup runs `yidt_core.py` to migrate once or verify the ledger.
- `website.service`: main account, OIDC, YCards; `parachute.service`: game; `webgame.service`: game hub and legacy redirects.
- Caddy routes `yidt.yitiao.org` to port 5010 and redirects `/points*` and `/games/points*` to the new domain. DNS is managed by the owner; Caddy obtains HTTPS after DNS reaches the server.
- `sudo /opt/vps-python/bin/python /root/yitiao/yidt_core.py` verifies the transaction chain, derived postings, balances and fixed supply after migration.
- The existing code archive backup includes `.yidt_key`; the database backup must include `userdata.db` and `account.db`. Restore DBs and encryption key together, with affected services stopped. Pre-migration backup: `/root/yitiao/backups/yidt-20261015/`.
- Do not roll back to old points code after new real transactions: old points tables are intentionally no longer updated.

Tests: `python -m unittest discover -s tests -p test_yidt.py` and, in a separate process, `python tests/test_yidt_account.py`. The latter exercises real TOTP and email-code validation with delivery mocked, plus mocked Google success/failure callbacks, expiry, CSRF, ownership, method switching, and single use. Browser fixture/check scripts use independent temporary databases and local ports 5081–5083. They must never be registered in production apps. Screenshots and browser result are in `tests/yidt-artifacts/`. QR decoding fallback uses jsQR 1.4.0, with its Apache-2.0 license retained in `static/yidt-jsqr-LICENSE.txt`.

UI follow-up checks: `python tests/test_yidt_account.py`, the wallet-pages and YCards receipt integration cases in `test_yidt.py`, and `node tests/yidt_ui_check.mjs` against the isolated browser fixture. The latter checks compact activity UI, wallet-domain PIN setup, own-wallet explorer history, and receipt placement.

YIDT also supports independent payment passkeys at `/passkeys/`, up to 8 per account. PIN-protected actions prefer these credentials and offer PIN fallback after cancellation. Main-site and YIDT passkey credentials are separate; see [PASSKEYS.md](PASSKEYS.md) for setup, isolation, and tests.
