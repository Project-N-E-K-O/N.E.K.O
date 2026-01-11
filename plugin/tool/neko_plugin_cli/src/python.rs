use pyo3::prelude::*;

#[pyfunction]
fn py_add(left: u64, right: u64) -> u64 {
    crate::add(left, right)
}

#[pyfunction]
fn py_version() -> &'static str {
    crate::version()
}

#[pymodule]
pub fn neko_plugin_cli(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_add, m)?)?;
    m.add_function(wrap_pyfunction!(py_version, m)?)?;
    Ok(())
}
