pub fn add(left: u64, right: u64) -> u64 {
    left + right
}

pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[cfg(feature = "python")]
mod python;

#[cfg(feature = "python")]
pub use python::neko_plugin_cli;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_add() {
        assert_eq!(add(2, 2), 4);
    }
}
