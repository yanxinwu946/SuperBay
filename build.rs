fn main() {
    println!("cargo:rerun-if-changed=assets/superbay.rc");
    println!("cargo:rerun-if-changed=assets/superbay.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        embed_resource::compile("assets/superbay.rc", embed_resource::NONE)
            .manifest_optional()
            .expect("failed to embed the SuperBay application icon");
    }
}
