use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{Context, Result};
use chrono::{SecondsFormat, Utc};
use directories::ProjectDirs;
use globset::{Glob, GlobSet, GlobSetBuilder};
use md5::Context as Md5Context;
use rayon::prelude::*;
use regex::Regex;
use serde::{Deserialize, Serialize};
use semver::{Version, VersionReq};
use walkdir::WalkDir;
use zip::read::ZipArchive;
use zip::write::FileOptions;
use zip::CompressionMethod;

#[derive(Debug, Clone, Default)]
pub(crate) struct BundleMeta {
    pub(crate) name: Option<String>,
    pub(crate) version: Option<String>,
    pub(crate) author: Option<String>,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct CheckFlags {
    pub(crate) id: bool,
    pub(crate) deps: bool,
    pub(crate) base: bool,
}

#[derive(Debug, Serialize, Clone)]
pub(crate) struct UnpackPreviewItem {
    pub(crate) id: String,
    pub(crate) folder: String,
    pub(crate) will_install: bool,
    pub(crate) reason: String,
}

pub(crate) fn resolve_check_flags(id: bool, deps: bool, base: bool) -> CheckFlags {
    if id || deps || base {
        return CheckFlags { id, deps, base };
    }
    CheckFlags {
        id: true,
        deps: true,
        base: true,
    }
}

#[derive(Debug, Serialize)]
pub(crate) struct CheckReport {
    pub(crate) sdk_version: String,
    pub(crate) plugins_checked: usize,
    pub(crate) errors: Vec<String>,
    pub(crate) warnings: Vec<String>,
    pub(crate) python_online: Option<PythonOnlineReport>,
}

#[derive(Debug, Serialize)]
pub(crate) struct PythonOnlineReport {
    pub(crate) enabled: bool,
    pub(crate) uv_found: bool,
    pub(crate) requirements_in: String,
    pub(crate) compiled_txt: String,
    pub(crate) exit_code: Option<i32>,
}

#[derive(Debug, Clone)]
struct PluginDependencyDecl {
    id: String,
    recommended: Option<String>,
    supported: Option<String>,
    untested: Option<String>,
    conflicts: Vec<String>,
}

#[derive(Debug, Clone)]
struct PluginSdkDecl {
    recommended: Option<String>,
    supported: Option<String>,
    untested: Option<String>,
    conflicts: Vec<String>,
}

#[derive(Debug, Clone)]
struct PluginRecord {
    folder: String,
    id: String,
    name: String,
    version: String,
    entry: String,
    sdk: PluginSdkDecl,
    deps: Vec<PluginDependencyDecl>,
}

pub(crate) fn read_sdk_version(repo_root: &Path) -> Result<Version> {
    let path = repo_root.join("plugin").join("sdk").join("version.py");
    let text = fs::read_to_string(&path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let re = Regex::new(r#"SDK_VERSION\s*=\s*\"([^\"]+)\""#)?;
    let caps = re
        .captures(&text)
        .ok_or_else(|| anyhow::anyhow!("failed to find SDK_VERSION in {}", path.display()))?;
    let raw = caps.get(1).map(|m| m.as_str()).unwrap_or("0.0.0");
    Ok(Version::parse(raw).with_context(|| format!("invalid SDK_VERSION '{raw}'"))?)
}

fn parse_req(req: &str) -> Result<VersionReq> {
    VersionReq::parse(req).with_context(|| format!("invalid version requirement: {req}"))
}

fn any_req_matches(reqs: &[String], v: &Version) -> bool {
    reqs.iter().filter_map(|s| VersionReq::parse(s).ok()).any(|r| r.matches(v))
}

pub(crate) fn run_checks(
    plugins_dir: &Path,
    plugin_id: Option<&str>,
    sdk_version: &Version,
    checks: CheckFlags,
) -> Result<CheckReport> {
    let mut plugins = read_plugin_records(plugins_dir, plugin_id)?;
    let plugins_checked = plugins.len();

    let mut errors: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();

    if checks.id {
        check_id_conflicts(&plugins, &mut errors);
    }
    if checks.base {
        check_sdk_compat(&plugins, sdk_version, &mut errors, &mut warnings)?;
    }
    if checks.deps {
        check_dependencies(&plugins, &mut errors, &mut warnings)?;
    }

    errors.sort();
    warnings.sort();

    plugins.sort_by(|a, b| a.id.cmp(&b.id));

    Ok(CheckReport {
        sdk_version: sdk_version.to_string(),
        plugins_checked,
        errors,
        warnings,
        python_online: None,
    })
}

fn resolve_cache_dir(repo_root: &Path, override_dir: Option<&Path>) -> PathBuf {
    if let Some(p) = override_dir {
        return p.to_path_buf();
    }
    if let Some(dirs) = ProjectDirs::from("io", "neko", "neko_plugin_cli") {
        return dirs.cache_dir().to_path_buf();
    }
    repo_root.join(".cache")
}

pub(crate) fn run_python_online_check(
    repo_root: &Path,
    plugins_dir: &Path,
    plugin_id: Option<&str>,
    strict: bool,
    cache_dir_override: Option<&Path>,
) -> Result<(PythonOnlineReport, Vec<String>, Vec<String>)> {
    let plugins = read_plugin_records(plugins_dir, plugin_id)?;

    let cache_root = resolve_cache_dir(repo_root, cache_dir_override)
        .join("neko_plugin_cli")
        .join("check_python");
    fs::create_dir_all(&cache_root)
        .with_context(|| format!("failed to create cache dir {}", cache_root.display()))?;

    let requirements_in = cache_root.join("requirements.in");
    let compiled_txt = cache_root.join("compiled.txt");
    let stderr_txt = cache_root.join("compile.stderr.txt");

    let _ = fs::remove_file(&compiled_txt);
    let _ = fs::remove_file(&stderr_txt);

    let mut reqs: Vec<String> = Vec::new();
    reqs.extend(read_pyproject_dependencies(&repo_root.join("pyproject.toml"))?);

    for p in &plugins {
        let pp = repo_root
            .join("plugin")
            .join("plugins")
            .join(&p.folder)
            .join("pyproject.toml");
        if pp.is_file() {
            reqs.extend(read_pyproject_dependencies(&pp)?);
        }
    }

    reqs.retain(|r| {
        let s = r.trim();
        if s.is_empty() {
            return false;
        }
        let lower = s.to_ascii_lowercase();
        !(lower == "n.e.k.o" || lower.starts_with("n.e.k.o ") || lower.starts_with("n.e.k.o;")
            || lower == "n-e-k-o" || lower.starts_with("n-e-k-o ") || lower.starts_with("n-e-k-o;"))
    });

    reqs.sort();
    reqs.dedup();

    let mut f = fs::File::create(&requirements_in)
        .with_context(|| format!("failed to write {}", requirements_in.display()))?;
    writeln!(f, "-e {}", repo_root.display())?;
    for r in &reqs {
        if r.trim().is_empty() {
            continue;
        }
        writeln!(f, "{}", r)?;
    }

    let mut errors: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();

    let output = Command::new("uv")
        .arg("pip")
        .arg("compile")
        .arg(requirements_in.as_os_str())
        .arg("-o")
        .arg(compiled_txt.as_os_str())
        .output();

    match output {
        Err(e) => {
            let msg = format!("python-online check skipped: failed to execute uv ({})", e);
            if strict {
                errors.push(msg);
            } else {
                warnings.push(msg);
            }
            Ok((
                PythonOnlineReport {
                    enabled: true,
                    uv_found: false,
                    requirements_in: requirements_in.display().to_string(),
                    compiled_txt: compiled_txt.display().to_string(),
                    exit_code: None,
                },
                errors,
                warnings,
            ))
        }
        Ok(out) => {
            let code = out.status.code();
            if !out.stderr.is_empty() {
                let _ = fs::write(&stderr_txt, &out.stderr);
            }
            if !out.status.success() {
                let stderr = String::from_utf8_lossy(&out.stderr);
                errors.push(format!(
                    "python-online dependency resolution failed (see {}): {}",
                    stderr_txt.display(),
                    stderr.lines().take(20).collect::<Vec<_>>().join("\n")
                ));
            }
            Ok((
                PythonOnlineReport {
                    enabled: true,
                    uv_found: true,
                    requirements_in: requirements_in.display().to_string(),
                    compiled_txt: compiled_txt.display().to_string(),
                    exit_code: code,
                },
                errors,
                warnings,
            ))
        }
    }
}

fn read_pyproject_dependencies(pyproject_path: &Path) -> Result<Vec<String>> {
    let txt = fs::read_to_string(pyproject_path)
        .with_context(|| format!("failed to read {}", pyproject_path.display()))?;
    let val: toml::Value = toml::from_str(&txt)
        .with_context(|| format!("failed to parse {}", pyproject_path.display()))?;
    let deps = val
        .get("project")
        .and_then(|v| v.get("dependencies"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    Ok(deps)
}

fn read_plugin_records(plugins_dir: &Path, plugin_id: Option<&str>) -> Result<Vec<PluginRecord>> {
    let mut out = Vec::new();
    if !plugins_dir.is_dir() {
        return Ok(out);
    }

    for entry in fs::read_dir(plugins_dir)
        .with_context(|| format!("failed to read dir {}", plugins_dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        let folder = match path.file_name().and_then(|v| v.to_str()) {
            Some(s) => s.to_string(),
            None => continue,
        };

        let plugin_toml = path.join("plugin.toml");
        if !plugin_toml.is_file() {
            continue;
        }

        let txt = fs::read_to_string(&plugin_toml)
            .with_context(|| format!("failed to read {}", plugin_toml.display()))?;
        let val: toml::Value = toml::from_str(&txt)
            .with_context(|| format!("failed to parse {}", plugin_toml.display()))?;
        let plugin = val.get("plugin");

        let id = plugin
            .and_then(|v| v.get("id"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        if let Some(want) = plugin_id {
            if id != want {
                continue;
            }
        }

        let name = plugin
            .and_then(|v| v.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or(&id)
            .to_string();
        let version = plugin
            .and_then(|v| v.get("version"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let entry_str = plugin
            .and_then(|v| v.get("entry"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let sdk_tbl = val.get("plugin").and_then(|p| p.get("sdk"));
        let sdk = PluginSdkDecl {
            recommended: sdk_tbl
                .and_then(|v| v.get("recommended"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            supported: sdk_tbl
                .and_then(|v| v.get("supported"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            untested: sdk_tbl
                .and_then(|v| v.get("untested"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            conflicts: sdk_tbl
                .and_then(|v| v.get("conflicts"))
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|x| x.as_str().map(|s| s.to_string()))
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default(),
        };

        let deps = val
            .get("plugin")
            .and_then(|p| p.get("dependency"))
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|d| {
                        let id = d.get("id")?.as_str()?.to_string();
                        let conflicts = d
                            .get("conflicts")
                            .and_then(|v| v.as_array())
                            .map(|c| {
                                c.iter()
                                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                                    .collect::<Vec<_>>()
                            })
                            .unwrap_or_default();
                        Some(PluginDependencyDecl {
                            id,
                            recommended: d.get("recommended").and_then(|v| v.as_str()).map(|s| s.to_string()),
                            supported: d.get("supported").and_then(|v| v.as_str()).map(|s| s.to_string()),
                            untested: d.get("untested").and_then(|v| v.as_str()).map(|s| s.to_string()),
                            conflicts,
                        })
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();

        out.push(PluginRecord {
            folder,
            id,
            name,
            version,
            entry: entry_str,
            sdk,
            deps,
        });
    }

    out.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(out)
}

fn check_id_conflicts(plugins: &[PluginRecord], errors: &mut Vec<String>) {
    use std::collections::HashMap;
    let mut map: HashMap<&str, Vec<&str>> = HashMap::new();
    for p in plugins {
        map.entry(&p.id).or_default().push(&p.folder);
    }
    for (id, folders) in map {
        if folders.len() > 1 {
            errors.push(format!(
                "plugin id conflict: id={} folders={}",
                id,
                folders.join(",")
            ));
        }
    }
}

fn check_sdk_compat(
    plugins: &[PluginRecord],
    sdk_version: &Version,
    errors: &mut Vec<String>,
    warnings: &mut Vec<String>,
) -> Result<()> {
    for p in plugins {
        if any_req_matches(&p.sdk.conflicts, sdk_version) {
            errors.push(format!(
                "plugin {} conflicts with SDK_VERSION {} (conflicts={:?})",
                p.id, sdk_version, p.sdk.conflicts
            ));
            continue;
        }

        let supported_ok = p
            .sdk
            .supported
            .as_deref()
            .map(|r| parse_req(r).map(|req| req.matches(sdk_version)))
            .transpose()?
            .unwrap_or(true);

        if supported_ok {
            continue;
        }

        let untested_ok = p
            .sdk
            .untested
            .as_deref()
            .map(|r| parse_req(r).map(|req| req.matches(sdk_version)))
            .transpose()?
            .unwrap_or(false);

        if untested_ok {
            warnings.push(format!(
                "plugin {} SDK_VERSION {} is in untested range ({})",
                p.id,
                sdk_version,
                p.sdk.untested.clone().unwrap_or_default()
            ));
        } else {
            errors.push(format!(
                "plugin {} SDK_VERSION {} not supported (supported={:?} untested={:?})",
                p.id, sdk_version, p.sdk.supported, p.sdk.untested
            ));
        }
    }
    Ok(())
}

pub(crate) fn preview_unpack(
    zip_path: &Path,
    dest_dir: &Path,
    force: bool,
    excludes: &GlobSet,
) -> Result<Vec<UnpackPreviewItem>> {
    let f = fs::File::open(zip_path)
        .with_context(|| format!("failed to open zip {}", zip_path.display()))?;
    let mut archive = ZipArchive::new(f).context("failed to read zip")?;

    let manifest = read_manifest(&mut archive)?;

    let mut items = Vec::new();

    for p in &manifest.plugins {
        let folder_rel = p.folder.trim_end_matches('/');
        let folder_name = folder_rel
            .split('/')
            .nth(1)
            .ok_or_else(|| anyhow::anyhow!("invalid manifest folder: {}", p.folder))?
            .to_string();

        let target_folder = dest_dir.join(&folder_name);

        if !target_folder.is_dir() {
            items.push(UnpackPreviewItem {
                id: p.id.clone(),
                folder: folder_name,
                will_install: true,
                reason: "destination folder does not exist; will install / 目标目录不存在，将安装".to_string(),
            });
            continue;
        }

        // Folder exists already
        if let Some(md5_expected) = &p.md5 {
            let md5_local = folder_md5(&target_folder, excludes)?;
            if &md5_local == md5_expected {
                items.push(UnpackPreviewItem {
                    id: p.id.clone(),
                    folder: folder_name,
                    will_install: false,
                    reason:
                        "existing plugin is identical (md5 match); will skip / 已有插件 md5 一致，将跳过"
                            .to_string(),
                });
                continue;
            }

            if !force {
                items.push(UnpackPreviewItem {
                    id: p.id.clone(),
                    folder: folder_name,
                    will_install: false,
                    reason:
                        "existing plugin differs; use --force to overwrite / 已有插件不同，需使用 --force 覆盖"
                            .to_string(),
                });
            } else {
                items.push(UnpackPreviewItem {
                    id: p.id.clone(),
                    folder: folder_name,
                    will_install: true,
                    reason:
                        "existing plugin differs; will overwrite (--force) / 已有插件不同，将使用 --force 覆盖"
                            .to_string(),
                });
            }
        } else {
            // No md5 info in manifest
            if !force {
                items.push(UnpackPreviewItem {
                    id: p.id.clone(),
                    folder: folder_name,
                    will_install: false,
                    reason:
                        "existing folder without md5; use --force to overwrite / 目标目录已存在且无 md5，需使用 --force 覆盖"
                            .to_string(),
                });
            } else {
                items.push(UnpackPreviewItem {
                    id: p.id.clone(),
                    folder: folder_name,
                    will_install: true,
                    reason:
                        "existing folder without md5; will overwrite (--force) / 目标目录已存在且无 md5，将使用 --force 覆盖"
                            .to_string(),
                });
            }
        }
    }

    Ok(items)
}

fn check_dependencies(
    plugins: &[PluginRecord],
    errors: &mut Vec<String>,
    warnings: &mut Vec<String>,
) -> Result<()> {
    use std::collections::HashMap;
    let mut by_id: HashMap<&str, &PluginRecord> = HashMap::new();
    for p in plugins {
        by_id.insert(&p.id, p);
    }

    for p in plugins {
        let pv = Version::parse(&p.version).unwrap_or_else(|_| Version::new(0, 0, 0));
        let _ = pv;

        for dep in &p.deps {
            let target = match by_id.get(dep.id.as_str()) {
                Some(t) => *t,
                None => {
                    errors.push(format!("plugin {} depends on missing plugin {}", p.id, dep.id));
                    continue;
                }
            };

            let tv = match Version::parse(&target.version) {
                Ok(v) => v,
                Err(_) => {
                    errors.push(format!(
                        "plugin {} dependency {} has invalid version {}",
                        p.id, dep.id, target.version
                    ));
                    continue;
                }
            };

            if any_req_matches(&dep.conflicts, &tv) {
                errors.push(format!(
                    "plugin {} dependency {} version {} hits conflicts {:?}",
                    p.id, dep.id, tv, dep.conflicts
                ));
                continue;
            }

            let supported_ok = dep
                .supported
                .as_deref()
                .map(|r| parse_req(r).map(|req| req.matches(&tv)))
                .transpose()?
                .unwrap_or(true);

            if supported_ok {
                continue;
            }

            let untested_ok = dep
                .untested
                .as_deref()
                .map(|r| parse_req(r).map(|req| req.matches(&tv)))
                .transpose()?
                .unwrap_or(false);

            if untested_ok {
                warnings.push(format!(
                    "plugin {} dependency {} version {} is in untested range ({})",
                    p.id,
                    dep.id,
                    tv,
                    dep.untested.clone().unwrap_or_default()
                ));
            } else {
                errors.push(format!(
                    "plugin {} dependency {} version {} not supported (supported={:?} untested={:?})",
                    p.id, dep.id, tv, dep.supported, dep.untested
                ));
            }
        }
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub(crate) struct InfoOutput {
    pub(crate) neko_version: String,
    pub(crate) repo_root: PathBuf,
    pub(crate) plugins: Vec<PluginMeta>,
}

#[derive(Debug, Serialize)]
pub(crate) struct PluginMeta {
    pub(crate) id: String,
    pub(crate) version: String,
    pub(crate) entry: String,
}

#[derive(Debug, Serialize, Clone)]
struct Manifest {
    format_version: u32,
    neko_base_version: String,
    packed_at: String,
    root_layout: String,
    bundle: Option<ManifestBundle>,
    bundle_profiles_root: Option<String>,
    plugins: Vec<ManifestPlugin>,
}

#[derive(Debug, Serialize, Clone)]
struct ManifestBundle {
    name: String,
    version: Option<String>,
    author: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
struct ManifestPlugin {
    id: String,
    name: String,
    version: String,
    entry: String,
    folder: String,
    md5: Option<String>,
    bundled_profiles: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ManifestDe {
    format_version: u32,
    neko_base_version: String,
    packed_at: String,
    root_layout: String,
    bundle: Option<ManifestBundleDe>,
    bundle_profiles_root: Option<String>,
    plugins: Vec<ManifestPluginDe>,
}

#[derive(Debug, Deserialize)]
struct ManifestBundleDe {
    name: String,
    version: Option<String>,
    author: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ManifestPluginDe {
    id: String,
    name: String,
    version: String,
    entry: String,
    folder: String,
    md5: Option<String>,
    bundled_profiles: Option<Vec<String>>,
}

fn sanitize_for_filename(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        let keep = ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.');
        out.push(if keep { ch } else { '_' });
    }
    if out.is_empty() {
        "bundle".to_string()
    } else {
        out
    }
}

fn derive_bundle_name(out_path: &Path) -> String {
    out_path
        .file_stem()
        .and_then(|s| s.to_str())
        .map(|s| sanitize_for_filename(s))
        .unwrap_or_else(|| "bundle".to_string())
}

fn read_file_to_zip<W: Write + std::io::Seek>(
    zip: &mut zip::ZipWriter<W>,
    zip_path: &str,
    src: &Path,
    options: FileOptions<()>,
) -> Result<()> {
    zip.start_file(zip_path, options)?;
    let mut f = fs::File::open(src).with_context(|| format!("failed to open {}", src.display()))?;
    let mut buf = [0u8; 1024 * 64];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        zip.write_all(&buf[..n])?;
    }
    Ok(())
}

fn collect_profile_files(plugin_dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    let p1 = plugin_dir.join("profiles.toml");
    if p1.is_file() {
        out.push(p1);
    }
    let pdir = plugin_dir.join("profiles");
    if pdir.is_dir() {
        for e in WalkDir::new(&pdir).follow_links(false) {
            if let Ok(e) = e {
                if e.file_type().is_file() {
                    out.push(e.path().to_path_buf());
                }
            }
        }
    }
    out.sort();
    out
}

#[derive(Debug, Clone)]
pub(crate) struct PluginPackItem {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) version: String,
    pub(crate) entry: String,
    pub(crate) folder: String,
    pub(crate) path: PathBuf,
    pub(crate) md5: Option<String>,
}

pub(crate) fn find_repo_root(mut start: PathBuf) -> Result<PathBuf> {
    for _ in 0..10 {
        let pyproject = start.join("pyproject.toml");
        let plugins_dir = start.join("plugin").join("plugins");
        if pyproject.is_file() && plugins_dir.is_dir() {
            return Ok(start);
        }
        if !start.pop() {
            break;
        }
    }
    anyhow::bail!("failed to locate repo root from cwd")
}

fn read_neko_base_version(repo_root: &Path) -> Result<String> {
    let pyproject_path = repo_root.join("pyproject.toml");
    let pyproject_text = fs::read_to_string(&pyproject_path)
        .with_context(|| format!("failed to read {}", pyproject_path.display()))?;
    let pyproject: toml::Value = toml::from_str(&pyproject_text)
        .with_context(|| format!("failed to parse {}", pyproject_path.display()))?;
    Ok(pyproject
        .get("project")
        .and_then(|v| v.get("version"))
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string())
}

pub(crate) fn collect_info(root: Option<&Path>) -> Result<InfoOutput> {
    let repo_root = match root {
        Some(p) => p.to_path_buf(),
        None => find_repo_root(std::env::current_dir().context("failed to get cwd")?)?,
    };

    let pyproject_path = repo_root.join("pyproject.toml");
    let pyproject_text = fs::read_to_string(&pyproject_path)
        .with_context(|| format!("failed to read {}", pyproject_path.display()))?;

    let pyproject: toml::Value = toml::from_str(&pyproject_text)
        .with_context(|| format!("failed to parse {}", pyproject_path.display()))?;

    let neko_version = pyproject
        .get("project")
        .and_then(|v| v.get("version"))
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    let plugins_dir = repo_root.join("plugin").join("plugins");
    let plugins = scan_plugins(&plugins_dir)?;

    Ok(InfoOutput {
        neko_version,
        repo_root,
        plugins,
    })
}

fn scan_plugins(plugins_dir: &Path) -> Result<Vec<PluginMeta>> {
    let mut out = Vec::new();
    if !plugins_dir.is_dir() {
        return Ok(out);
    }

    for entry in fs::read_dir(plugins_dir)
        .with_context(|| format!("failed to read dir {}", plugins_dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        let plugin_toml = path.join("plugin.toml");
        if !plugin_toml.is_file() {
            continue;
        }

        let txt = fs::read_to_string(&plugin_toml)
            .with_context(|| format!("failed to read {}", plugin_toml.display()))?;
        let val: toml::Value = toml::from_str(&txt)
            .with_context(|| format!("failed to parse {}", plugin_toml.display()))?;

        let plugin = val.get("plugin");
        let id = plugin
            .and_then(|v| v.get("id"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let version = plugin
            .and_then(|v| v.get("version"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let entry_str = plugin
            .and_then(|v| v.get("entry"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        out.push(PluginMeta {
            id,
            version,
            entry: entry_str,
        });
    }

    out.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(out)
}

pub(crate) fn scan_plugins_for_pack(
    plugins_dir: &Path,
    plugin_ids: Option<&[String]>,
) -> Result<Vec<PluginPackItem>> {
    let mut out = Vec::new();
    if !plugins_dir.is_dir() {
        return Ok(out);
    }

    for entry in fs::read_dir(plugins_dir)
        .with_context(|| format!("failed to read dir {}", plugins_dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        let folder = match path.file_name().and_then(|v| v.to_str()) {
            Some(s) => s.to_string(),
            None => continue,
        };

        let plugin_toml = path.join("plugin.toml");
        if !plugin_toml.is_file() {
            continue;
        }

        let txt = fs::read_to_string(&plugin_toml)
            .with_context(|| format!("failed to read {}", plugin_toml.display()))?;
        let val: toml::Value = toml::from_str(&txt)
            .with_context(|| format!("failed to parse {}", plugin_toml.display()))?;
        let plugin = val.get("plugin");

        let id = plugin
            .and_then(|v| v.get("id"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        if let Some(wants) = plugin_ids {
            if !wants.is_empty() && !wants.iter().any(|w| w == &id) {
                continue;
            }
        }

        let name = plugin
            .and_then(|v| v.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or(&id)
            .to_string();
        let version = plugin
            .and_then(|v| v.get("version"))
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let entry_str = plugin
            .and_then(|v| v.get("entry"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        out.push(PluginPackItem {
            id,
            name,
            version,
            entry: entry_str,
            folder,
            path,
            md5: None,
        });
    }

    out.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(out)
}

pub(crate) fn default_pack_output(plugins: &[PluginPackItem], single: bool) -> PathBuf {
    if single {
        let p = &plugins[0];
        return PathBuf::from(format!("neko_plugin_{}_{}.zip", p.id, p.version));
    }
    let ts = Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true);
    PathBuf::from(format!("neko_plugins_bundle_{}.zip", ts.replace(':', "-")))
}

pub(crate) fn list_packable_plugin_ids(plugins_dir: &Path) -> Result<Vec<String>> {
    let plugins = scan_plugins_for_pack(plugins_dir, None)?;
    Ok(plugins.into_iter().map(|p| p.id).collect())
}

pub(crate) fn build_excludes(extra: &[String]) -> Result<GlobSet> {
    let mut b = GlobSetBuilder::new();
    for pat in [
        "**/__pycache__/**",
        "**/*.pyc",
        "**/.git/**",
        "**/.venv/**",
        "**/log/**",
        "**/logs/**",
    ] {
        b.add(Glob::new(pat)?);
    }
    for pat in extra {
        b.add(Glob::new(pat)?);
    }
    Ok(b.build()?)
}

pub(crate) fn folder_md5(plugin_dir: &Path, excludes: &GlobSet) -> Result<String> {
    let mut files: Vec<PathBuf> = Vec::new();
    for e in WalkDir::new(plugin_dir).follow_links(false) {
        let e = e?;
        if !e.file_type().is_file() {
            continue;
        }
        let p = e.path().to_path_buf();
        let rel = p
            .strip_prefix(plugin_dir)
            .unwrap_or(e.path())
            .to_string_lossy()
            .replace('\\', "/");
        if excludes.is_match(&rel) {
            continue;
        }
        files.push(p);
    }

    files.sort_by(|a, b| {
        a.strip_prefix(plugin_dir)
            .unwrap_or(a)
            .to_string_lossy()
            .cmp(&b.strip_prefix(plugin_dir).unwrap_or(b).to_string_lossy())
    });

    let mut hasher = Md5Context::new();
    for p in files {
        let rel = p
            .strip_prefix(plugin_dir)
            .unwrap_or(&p)
            .to_string_lossy()
            .replace('\\', "/");
        hasher.consume(rel.as_bytes());
        hasher.consume([0u8]);

        let mut f = fs::File::open(&p).with_context(|| format!("failed to open {}", p.display()))?;
        let mut buf = [0u8; 1024 * 64];
        loop {
            let n = f.read(&mut buf)?;
            if n == 0 {
                break;
            }
            hasher.consume(&buf[..n]);
        }
        hasher.consume([0u8]);
    }

    Ok(format!("{:x}", hasher.compute()))
}

pub(crate) fn pack_to_zip(
    out_path: &Path,
    plugins: &[PluginPackItem],
    excludes: &GlobSet,
    bundle_meta: BundleMeta,
) -> Result<()> {
    let tmp_path = out_path.with_extension("zip.tmp");
    let f = fs::File::create(&tmp_path).with_context(|| format!("failed to create {}", tmp_path.display()))?;
    let mut zip = zip::ZipWriter::new(f);

    let options = FileOptions::<()>::default().compression_method(CompressionMethod::Deflated);

    let repo_root = plugins
        .first()
        .and_then(|p| p.path.parent())
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .map(Path::to_path_buf)
        .context("failed to infer repo root from plugin path")?;

    let neko_base_version = read_neko_base_version(&repo_root)?;

    let bundle_name = bundle_meta
        .name
        .clone()
        .unwrap_or_else(|| derive_bundle_name(out_path));
    let bundle_name_safe = sanitize_for_filename(&bundle_name);
    let bundle_version_safe = bundle_meta
        .version
        .as_deref()
        .map(sanitize_for_filename)
        .unwrap_or_else(|| "unknown".to_string());
    let bundle_profiles_root = format!("bundle_profiles/{}/", bundle_name_safe);

    // Pre-compute bundled profile paths for each plugin.
    let mut bundled_profiles_map: Vec<Vec<String>> = Vec::new();
    for p in plugins {
        let mut paths: Vec<String> = Vec::new();
        for src in collect_profile_files(&p.path) {
            let rel = src
                .strip_prefix(&p.path)
                .unwrap_or(&src)
                .to_string_lossy()
                .replace('\\', "/");
            let rel_name = sanitize_for_filename(&rel.replace('/', "__"));
            let zip_path = format!(
                "{}plugins/{}/{}__{}__{}__{}",
                bundle_profiles_root,
                sanitize_for_filename(&p.id),
                bundle_name_safe,
                bundle_version_safe,
                sanitize_for_filename(&p.id),
                rel_name
            );
            paths.push(zip_path);
        }
        bundled_profiles_map.push(paths);
    }

    let manifest = Manifest {
        format_version: 1,
        neko_base_version,
        packed_at: Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true),
        root_layout: "plugins/".to_string(),
        bundle: Some(ManifestBundle {
            name: bundle_name,
            version: bundle_meta.version,
            author: bundle_meta.author,
        }),
        bundle_profiles_root: Some(bundle_profiles_root.clone()),
        plugins: plugins
            .iter()
            .zip(bundled_profiles_map.iter())
            .map(|(p, paths)| ManifestPlugin {
                id: p.id.clone(),
                name: p.name.clone(),
                version: p.version.clone(),
                entry: p.entry.clone(),
                folder: format!("plugins/{}", p.folder),
                md5: p.md5.clone(),
                bundled_profiles: paths.clone(),
            })
            .collect(),
    };

    let manifest_text = toml::to_string(&manifest).context("failed to serialize manifest")?;
    zip.start_file("manifest.toml", options)?;
    zip.write_all(manifest_text.as_bytes())?;

    for plugin in plugins {
        let mut files: Vec<PathBuf> = Vec::new();
        for e in WalkDir::new(&plugin.path).follow_links(false) {
            let e = e?;
            if !e.file_type().is_file() {
                continue;
            }
            let p = e.path().to_path_buf();
            let rel = p
                .strip_prefix(&plugin.path)
                .unwrap_or(e.path())
                .to_string_lossy()
                .replace('\\', "/");
            if excludes.is_match(&rel) {
                continue;
            }
            files.push(p);
        }

        files.sort_by(|a, b| {
            a.strip_prefix(&plugin.path)
                .unwrap_or(a)
                .to_string_lossy()
                .cmp(&b.strip_prefix(&plugin.path).unwrap_or(b).to_string_lossy())
        });

        for p in files {
            let rel = p
                .strip_prefix(&plugin.path)
                .unwrap_or(&p)
                .to_string_lossy()
                .replace('\\', "/");
            let zip_path = format!("plugins/{}/{}", plugin.folder, rel);
            read_file_to_zip(&mut zip, &zip_path, &p, options)?;
        }
    }

    // Bundle profiles are stored under bundle_profiles/<bundle_name>/plugins/<plugin_id>/... with renamed files.
    for (plugin, zip_paths) in plugins.iter().zip(bundled_profiles_map.iter()) {
        let sources = collect_profile_files(&plugin.path);
        for (src, zip_path) in sources.iter().zip(zip_paths.iter()) {
            read_file_to_zip(&mut zip, zip_path, src, options)?;
        }
    }

    zip.finish()?;
    fs::rename(&tmp_path, out_path)
        .with_context(|| format!("failed to rename {} -> {}", tmp_path.display(), out_path.display()))?;
    Ok(())
}

fn read_manifest<R: Read + std::io::Seek>(archive: &mut ZipArchive<R>) -> Result<ManifestDe> {
    let mut file = archive
        .by_name("manifest.toml")
        .context("manifest.toml not found in zip")?;
    let mut buf = String::new();
    file.read_to_string(&mut buf)
        .context("failed to read manifest.toml")?;
    let m: ManifestDe = toml::from_str(&buf).context("failed to parse manifest.toml")?;
    Ok(m)
}

fn is_safe_rel_path(rel: &str) -> bool {
    let p = Path::new(rel);
    if p.is_absolute() {
        return false;
    }
    for c in p.components() {
        if matches!(c, std::path::Component::ParentDir | std::path::Component::Prefix(_)) {
            return false;
        }
    }
    true
}

pub(crate) fn unpack_zip(zip_path: &Path, dest_dir: &Path, force: bool, excludes: &GlobSet) -> Result<()> {
    fs::create_dir_all(dest_dir)
        .with_context(|| format!("failed to create dest dir {}", dest_dir.display()))?;

    let f = fs::File::open(zip_path)
        .with_context(|| format!("failed to open zip {}", zip_path.display()))?;
    let mut archive = ZipArchive::new(f).context("failed to read zip")?;

    let manifest = read_manifest(&mut archive)?;
    let root_layout = manifest.root_layout.trim_end_matches('/');

    // Map plugin_id -> folder_name (the folder under <dest_dir>)
    let mut id_to_folder: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for p in &manifest.plugins {
        let folder_rel = p.folder.trim_end_matches('/');
        let folder_name = folder_rel
            .split('/')
            .nth(1)
            .ok_or_else(|| anyhow::anyhow!("invalid manifest folder: {}", p.folder))?
            .to_string();
        id_to_folder.insert(p.id.clone(), folder_name);
    }

    let bundle_profiles_root = manifest
        .bundle_profiles_root
        .as_deref()
        .map(|s| s.trim_end_matches('/').to_string());

    use std::collections::HashSet;
    let mut skip_folders: HashSet<String> = HashSet::new();

    for p in &manifest.plugins {
        let folder_rel = p.folder.trim_end_matches('/');
        let folder_name = folder_rel
            .split('/')
            .nth(1)
            .ok_or_else(|| anyhow::anyhow!("invalid manifest folder: {}", p.folder))?
            .to_string();

        let target_folder = dest_dir.join(&folder_name);
        if target_folder.is_dir() {
            eprintln!("WARN: plugin folder already exists: {}", target_folder.display());

            if let Some(md5_expected) = &p.md5 {
                let md5_local = folder_md5(&target_folder, excludes)?;
                if &md5_local == md5_expected {
                    eprintln!("INFO: plugin '{}' is identical (md5 match), skipping", p.id);
                    skip_folders.insert(folder_name);
                    continue;
                }
            }

            if !force {
                eprintln!(
                    "WARN: plugin '{}' differs from existing; skipping (use --force to overwrite)",
                    p.id
                );
                skip_folders.insert(folder_name);
            }
        }
    }

    for i in 0..archive.len() {
        let mut file = archive.by_index(i)?;
        if !file.is_file() {
            continue;
        }

        let name = file.name().to_string();
        if name == "manifest.toml" {
            continue;
        }

        // 1) Normal plugin payload: <root_layout>/<folder>/<rel>
        let prefix_plugins = format!("{}/", root_layout);
        if name.starts_with(&prefix_plugins) {
            let remainder = &name[prefix_plugins.len()..];
            let mut parts = remainder.splitn(2, '/');
            let folder = match parts.next() {
                Some(s) if !s.is_empty() => s,
                _ => continue,
            };
            let rel = match parts.next() {
                Some(s) if !s.is_empty() => s,
                _ => continue,
            };

            if skip_folders.contains(folder) {
                continue;
            }

            if !is_safe_rel_path(rel) {
                eprintln!("WARN: skipped unsafe path in zip: {}", name);
                continue;
            }

            let out_path = dest_dir.join(folder).join(rel);
            if let Some(parent) = out_path.parent() {
                fs::create_dir_all(parent)?;
            }

            if out_path.exists() && !force {
                eprintln!("WARN: file conflict, skipping: {}", out_path.display());
                continue;
            }

            let mut out = fs::File::create(&out_path)
                .with_context(|| format!("failed to create {}", out_path.display()))?;
            std::io::copy(&mut file, &mut out)
                .with_context(|| format!("failed to write {}", out_path.display()))?;
            continue;
        }

        // 2) Bundled profiles payload: <bundle_profiles_root>/plugins/<plugin_id>/<renamed_file>
        if let Some(root) = &bundle_profiles_root {
            let prefix_profiles = format!("{}/plugins/", root);
            if name.starts_with(&prefix_profiles) {
                let remainder = &name[prefix_profiles.len()..];
                let mut parts = remainder.splitn(2, '/');
                let plugin_id = match parts.next() {
                    Some(s) if !s.is_empty() => s,
                    _ => continue,
                };
                let rel = match parts.next() {
                    Some(s) if !s.is_empty() => s,
                    _ => continue,
                };

                if !is_safe_rel_path(rel) {
                    eprintln!("WARN: skipped unsafe bundled profile path in zip: {}", name);
                    continue;
                }

                let Some(folder_name) = id_to_folder.get(plugin_id).cloned() else {
                    eprintln!("WARN: bundled profile references unknown plugin id: {}", plugin_id);
                    continue;
                };

                // Place bundled profiles inside plugin folder without touching user profiles.
                // Use a dedicated internal directory to avoid overwriting ./profiles and ./profiles.toml.
                let out_path = dest_dir
                    .join(folder_name)
                    .join("_bundle_profiles")
                    .join(root)
                    .join(rel);

                if let Some(parent) = out_path.parent() {
                    fs::create_dir_all(parent)?;
                }

                // Never overwrite bundled profiles unless --force.
                if out_path.exists() && !force {
                    continue;
                }

                let mut out = fs::File::create(&out_path)
                    .with_context(|| format!("failed to create {}", out_path.display()))?;
                std::io::copy(&mut file, &mut out)
                    .with_context(|| format!("failed to write {}", out_path.display()))?;
                continue;
            }
        }
    }

    Ok(())
}

pub(crate) fn compute_plugin_md5_for_pack(plugins: &mut [PluginPackItem], excludes: &GlobSet, no_md5: bool) -> Result<()> {
    if no_md5 {
        return Ok(());
    }
    plugins.par_iter_mut().try_for_each(|p| -> Result<()> {
        let md5 = folder_md5(&p.path, excludes)?;
        p.md5 = Some(md5);
        Ok(())
    })?;
    Ok(())
}
