; YiChat NSIS hooks (bundle.windows.nsis.installerHooks).
; The Tauri template already sets AppUserModelID = ${BUNDLEID} on the Start menu
; and desktop shortcuts and registers the yichat-open: protocol (deep-link schemes).
; These hooks add the per-user AppUserModelId registration that gives toasts
; their display name and icon even without a shortcut, and clean up on uninstall.

!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr HKCU "Software\Classes\AppUserModelId\${BUNDLEID}" "DisplayName" "${PRODUCTNAME}"
  WriteRegStr HKCU "Software\Classes\AppUserModelId\${BUNDLEID}" "IconUri" "$INSTDIR\toast-icon.png"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegKey HKCU "Software\Classes\AppUserModelId\${BUNDLEID}"
  ; Launch-at-login entries written by tauri-plugin-autostart (value name = product name).
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCTNAME}"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${PRODUCTNAME}"
!macroend
