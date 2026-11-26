fn main() {
    println!("cargo:rerun-if-changed=../assets/icon/drop_air_brand.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        let mut res = winresource::WindowsResource::new();
        res.set_icon("../assets/icon/drop_air_brand.ico");
        res.set("ProductName", "Drop Air");
        res.set("FileDescription", "Drop Air");
        res.compile().expect("could not embed the Windows icon");
    }
}
