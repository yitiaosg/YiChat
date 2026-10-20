# Passkeys

Main account settings expose `/account/passkeys/` before the two-factor section. Each account can register at most 8 credentials. Every management visit and mutation requires a fresh email or TOTP verification; passkeys and recovery codes cannot satisfy this prerequisite. If neither email nor TOTP is configured, the request is cancelled with a link back to account settings. The management page supports naming, renaming, and removing keys.

Main login offers discoverable passkey login, plus conditional autofill where the browser supports it (`autocomplete="username webauthn"`). A successful user-verified assertion logs in directly regardless of the login two-factor toggle. In sensitive verification, passkeys are offered first when registered, and the existing method picker remains available. Web pages cannot silently enumerate the user's stored credentials: the browser mediates credential selection and phone/security-key use.

YIDT exposes its own `/passkeys/` page. A six-digit payment PIN must exist; registration options, renaming, and deletion require that PIN. Resetting the PIN invalidates pending enrollment/payment challenges. Registered YIDT keys replace the PIN challenge for locked payments and other existing PIN-protected wallet actions, but never authorize main-site login. Fixed service payments request device verification on entry, then still require the user to confirm the payment. Transfers request verification after the transfer fields are filled. Cancellation, unavailable credentials, or unsupported browsers reveal the PIN fallback; the retry button starts a new request.

## Isolation and verification

- Main credentials: `account.db`, RP ID `yitiao.org`, origins `https://yitiao.org` and `https://www.yitiao.org`.
- YIDT credentials: `userdata.db`, RP ID `yidt.yitiao.org`, origin `https://yidt.yitiao.org`.
- Each database owns separate `passkey_users`, `passkey_credentials`, and `passkey_challenges` tables. User handles are random 32-byte values. Neither domain accepts the other's credentials or verification challenges.
- `passkeys.py` uses pinned `webauthn==2.8.0` with the existing `cryptography==48.0.0` runtime; `cbor2==5.9.0` and `pyOpenSSL==26.2.0` are pinned in requirements.
- Registration requests discoverable credentials and required user verification. There is no authenticator attachment restriction, allowing compatible platform, cross-device, and USB FIDO2 authenticators. No biometric data or authenticator private keys are stored on the server.
- Verification checks RP ID, exact origin, signature, user presence/verification, credential ownership, user handle, and signature counter. Cross-origin frames are rejected. Synced authenticators with zero counters remain supported by the verifier.
- Challenges are random, expire after 5 minutes, and are single use. They are tied to the browser session, user (except discoverable login), purpose, and operation context. Wallet assertions include a hash of the exact form path, business fields, and submit action; validation/consumption occurs in the wallet transaction. Changing transfer amount, recipient, or action invalidates the proof. Expired pending challenges are cleaned when new ones are issued.
- Registration count checks run inside `BEGIN IMMEDIATE`, including the final registration write, so concurrent requests cannot exceed 8.
- APIs require same-origin JSON requests and CSRF tokens. Existing wallet PIN attempt limits remain in force.

## Validation

Run separately:

```
python tests/test_passkeys.py
python tests/test_passkey_flows.py
python tests/test_yidt_account.py
python -m unittest discover -s tests -p test_yidt.py
```

The passkey unit tests generate real ES256 signatures and test invalid signatures, RP/origin mismatch, absent UV, replay, expiry, session/account/context binding, deletion, and concurrent enrollment quotas. Flow tests use isolated account/ledger databases.

`tests/passkeys_browser_check.mjs` uses Chrome's virtual CTAP2 platform authenticator against `tests/yidt_browser_fixture.py` on local ports 5081–5083. It checks actual browser credential creation/assertion APIs, two-factor prerequisites, direct login, isolation, locked service payment, direct transfer, cancellation-to-PIN fallback, retry, removal, and mobile layout. Run `tests/passkeys_usb_check.mjs` after it to register and authenticate using a virtual CTAP2 USB authenticator. These fixtures must never be installed as services. Physical-device unlock UI is owned by the browser/OS.

References: [WebAuthn specification](https://www.w3.org/TR/webauthn/), [py_webauthn registration](https://duo-labs.github.io/py_webauthn/registration.html), [authentication](https://duo-labs.github.io/py_webauthn/authentication.html).
