# Gift URLs and Android offline recovery — 2026-10-23

Gift chat bodies now contain only `https://chat.yitiao.org/gifts/<gift-id>`.
The stable reference has no username, current owner or card design. Minimal
business routing metadata still identifies the accepted gift to the gateway;
it contains no card rendering data. New gifts no longer write rendering
snapshots. Existing snapshot rows are left intact and are no longer served.
The room/history-authorized lookup resolves the current card regardless of a
later ownership transfer. Destroyed cards return HTTP 410 without card data.
Visible bubbles recheck on focus/resume and periodically; a revoked/destroyed
preview clears its renderer and closes any details modal. Opening the URL keeps
the existing authenticated room access rules.

The Android 0.3.1 notification worker was only scheduled every 15 minutes. The
0.3.2 update (code 10, same package/signature) adds an explicitly enabled first-
party foreground notification listener, with an ongoing notification and a
15-second metadata check while the chat screen is closed. It survives ordinary
Activity/task removal, uses a mutex with the existing fallback worker, and never
runs another Matrix sync or consumes to-device keys. Permission/enablement,
logout, mute/archive, current membership and existing server cursors remain in
force. Android battery settings can still affect background execution. The UI
exposes the application's background settings. Android 14+ uses the declared
specialUse foreground-service type for this user-enabled notification listener.

Two separate decryption recovery defects were found:

1. `decryptEventIfNeeded` skips a MatrixEvent that already contains a failed
   `m.bad.encrypted` clear event. Recovery now invokes the pinned SDK's actual
   retry path, preserving its cryptographic verification and emitting the normal
   decrypted event. App sync completion also retries previously failed events.
2. Restoring a correct backup key previously attempted to bootstrap/import
   cross-signing first. In a real isolated new-device test that failed with
   `importCrossSigningKeys failed to import the keys`, preventing Megolm backup
   restoration. Message recovery now validates the secret-storage key and loads
   the backup key directly; it does not reset or require cross-signing setup.

Validation:

- 9 frontend unit tests passed; TypeScript/Vite production build passed.
- 12 targeted gift/history/download backend tests passed. One unrelated existing test
  about a mocked redaction window was excluded after reproducing its identical
  failure against the original production source.
- A real isolated Synapse and PostgreSQL test exchanged encrypted messages,
  closed the receiving App-mode page, forced a new outbound Megolm session,
  reopened the same device and decrypted the offline message. A new device then
  restored the standard secret-storage backup with the correct recovery key and
  successfully decrypted both prior failed timeline messages. All four checks
  passed. No production account or message was used.
- Chromium exercised the actual gift dialog, conversation and shared card
  renderer with isolated API fixtures. It verified a URL-only message, layouts,
  failed message retries without another transfer, and removing the card canvas
  and open details after the API reports destruction.
- Android release build, 7 JVM tests and release lint passed. The final release also includes the updated
  notification capability/settings UI and stable APK download link.
- The dedicated task-removal notification instrumentation test is provided in
  `android/app/src/androidTest/.../BackgroundNotificationsTest.kt`. It passed
  on a fresh isolated API 28 emulator: the ongoing listener survived removal
  of the activity task and notified a subsequently accepted group message.
  API 33 hardware acceleration was unavailable in this host; this is not an
  assertion of physical-device/OEM battery-policy coverage.

Sources for platform/SDK behavior:
- https://developer.android.com/develop/background-work/services/fgs/service-types#special-use
- https://matrix-org.github.io/matrix-js-sdk/classes/matrix.IndexedDBStore.html

No device stores, recovery keys, archived messages or wallet balances are deleted
or reset by this deployment. APK upgrades must cover the existing installation;
no uninstall or data clearing is required.

Release follow-up: downloads now use GitHub Releases exclusively. The temporary
`/app/download` handler has been removed; no APK is served by YiChat.
