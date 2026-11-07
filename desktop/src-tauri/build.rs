fn main() {
    // Declaring the app manifest makes `yichat_bridge` require an explicit
    // capability grant (capabilities/main.json); nothing else is exposed.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["yichat_bridge"])),
    )
    .expect("failed to run tauri-build");
}
