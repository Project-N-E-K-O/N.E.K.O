use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};

use crate::core;
use crate::tui;

pub(crate) fn run() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Add { left, right } => {
            let out = neko_plugin_cli::add(left, right);
            println!("{out}");
        }
        Commands::Version => {
            println!("{}", neko_plugin_cli::version());
        }
        Commands::Info { root, json } => {
            let info = core::collect_info(root.as_deref())?;
            if json {
                println!("{}", serde_json::to_string_pretty(&info)?);
            } else {
                println!("N.E.K.O version: {}", info.neko_version);
                println!("Repo root: {}", info.repo_root.display());
                println!("Plugin count: {}", info.plugins.len());
                for p in &info.plugins {
                    println!("- {} v{} ({})", p.id, p.version, p.entry);
                }
            }
        }
        Commands::Pack {
            plugin_id,
            root,
            out,
            jobs,
            exclude,
            no_md5,
            bundle_name,
            bundle_version,
            bundle_author,
        } => {
            if let Some(n) = jobs {
                rayon::ThreadPoolBuilder::new().num_threads(n).build_global().ok();
            }

            let repo_root = match root {
                Some(p) => p,
                None => core::find_repo_root(std::env::current_dir().context("failed to get cwd")?)?,
            };

            let plugins_dir = repo_root.join("plugin").join("plugins");
            let excludes = core::build_excludes(&exclude)?;

            let plugin_ids_ref: Option<&[String]> = if plugin_id.is_empty() { None } else { Some(&plugin_id) };
            let mut plugins = core::scan_plugins_for_pack(&plugins_dir, plugin_ids_ref)?;
            if plugins.is_empty() {
                anyhow::bail!("no plugins found to pack");
            }

            core::compute_plugin_md5_for_pack(&mut plugins, &excludes, no_md5)?;

            let out_path = out.unwrap_or_else(|| core::default_pack_output(&plugins, !plugin_id.is_empty()));
            core::pack_to_zip(
                &out_path,
                &plugins,
                &excludes,
                core::BundleMeta {
                    name: bundle_name,
                    version: bundle_version,
                    author: bundle_author,
                },
            )?;
            println!("{}", out_path.display());
        }
        Commands::Check {
            plugin_id,
            root,
            json,
            id,
            deps,
            base,
            python,
            python_strict,
            cache_dir,
        } => {
            let repo_root = match root {
                Some(p) => p,
                None => core::find_repo_root(std::env::current_dir().context("failed to get cwd")?)?,
            };

            let plugins_dir = repo_root.join("plugin").join("plugins");
            let sdk_version = core::read_sdk_version(&repo_root)?;

            let checks = core::resolve_check_flags(id, deps, base);
            let mut report = core::run_checks(&plugins_dir, plugin_id.as_deref(), &sdk_version, checks)?;

            if python {
                let (py_rep, mut py_errs, mut py_warns) = core::run_python_online_check(
                    &repo_root,
                    &plugins_dir,
                    plugin_id.as_deref(),
                    python_strict,
                    cache_dir.as_deref(),
                )?;
                report.errors.append(&mut py_errs);
                report.warnings.append(&mut py_warns);
                report.python_online = Some(py_rep);
                report.errors.sort();
                report.warnings.sort();
            }

            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("SDK_VERSION: {}", report.sdk_version);
                println!("Plugins checked: {}", report.plugins_checked);
                println!("Errors: {}", report.errors.len());
                println!("Warnings: {}", report.warnings.len());
                for e in &report.errors {
                    println!("ERROR: {}", e);
                }
                for w in &report.warnings {
                    println!("WARN: {}", w);
                }
            }

            if !report.errors.is_empty() {
                anyhow::bail!("check failed");
            }
        }
        Commands::Unpack {
            zip_path,
            root,
            dest,
            force,
        } => {
            let repo_root = match root {
                Some(p) => p,
                None => core::find_repo_root(std::env::current_dir().context("failed to get cwd")?)?,
            };

            let dest_dir = dest.unwrap_or_else(|| repo_root.join("plugin").join("plugins"));
            let excludes = core::build_excludes(&[])?;

            let zip_path = resolve_zip_path(&zip_path, &repo_root)
                .with_context(|| format!("failed to locate zip: {}", zip_path.display()))?;
            core::unpack_zip(&zip_path, &dest_dir, force, &excludes)?;
            println!("{}", dest_dir.display());
        }

        Commands::Tui { root } => {
            tui::run(root)?;
        }
    }

    Ok(())
}

#[derive(Parser, Debug)]
#[command(name = "neko-plugin-cli")]
#[command(about = "N.E.K.O 插件 CLI（Rust，可选 Python 绑定） / N.E.K.O plugin CLI (Rust + optional Python bindings)")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    #[command(about = "自检：验证 CLI 与库函数连通性 / Sanity: verify CLI and library wiring")]
    Add {
        #[arg(help = "左操作数 / Left operand")]
        left: u64,
        #[arg(help = "右操作数 / Right operand")]
        right: u64,
    },

    #[command(about = "输出版本号 / Print version")]
    Version,

    #[command(about = "显示 N.E.K.O 基座版本与插件概览 / Show N.E.K.O base version and plugin summary")]
    Info {
        #[arg(long, help = "仓库根目录（可选，默认自动探测） / Repo root (optional, auto-detect by default)")]
        root: Option<PathBuf>,

        #[arg(long, help = "输出 JSON / Output JSON")]
        json: bool,
    },

    #[command(about = "打包插件为 zip（含 manifest 与 md5） / Pack plugins into zip (with manifest + md5)")]
    Pack {
        #[arg(help = "插件 ID（可多次指定；省略则打包全部插件） / Plugin id(s) (repeatable; omit to pack all)")]
        plugin_id: Vec<String>,

        #[arg(long, help = "仓库根目录（可选，默认自动探测） / Repo root (optional, auto-detect by default)")]
        root: Option<PathBuf>,

        #[arg(long, help = "输出 zip 路径（可选） / Output zip path (optional)")]
        out: Option<PathBuf>,

        #[arg(long, help = "md5 计算并行度（可选） / Parallel jobs for md5 (optional)")]
        jobs: Option<usize>,

        #[arg(long, help = "额外排除 glob（可多次指定） / Extra exclude globs (repeatable)")]
        exclude: Vec<String>,

        #[arg(long, help = "跳过 md5（更快但无法用于一致性跳过） / Skip md5 (faster, but no identical-skip)")]
        no_md5: bool,

        #[arg(long, help = "整合包名称（用于 profiles 命名空间与重命名；默认取输出 zip 文件名） / Bundle name (for profiles namespacing; default derived from output zip name)")]
        bundle_name: Option<String>,

        #[arg(long, help = "整合包版本（写入 manifest，并参与 profiles 重命名） / Bundle version (written to manifest and used in profile renaming)")]
        bundle_version: Option<String>,

        #[arg(long, help = "整合包作者（写入 manifest，并参与 profiles 重命名） / Bundle author (written to manifest and used in profile renaming)")]
        bundle_author: Option<String>,
    },

    #[command(about = "检查插件冲突与兼容性 / Check plugin conflicts and compatibility")]
    Check {
        #[arg(help = "插件 ID（可选；省略则检查全部插件） / Plugin id (optional; omit to check all)")]
        plugin_id: Option<String>,

        #[arg(long, help = "仓库根目录（可选，默认自动探测） / Repo root (optional, auto-detect by default)")]
        root: Option<PathBuf>,

        #[arg(long, help = "输出 JSON / Output JSON")]
        json: bool,

        #[arg(long, help = "只检查插件 ID 冲突 / Only check plugin id conflicts")]
        id: bool,

        #[arg(long, help = "只检查插件依赖关系 / Only check plugin dependencies")]
        deps: bool,

        #[arg(long, help = "只检查 SDK(base) 兼容性 / Only check SDK(base) compatibility")]
        base: bool,

        #[arg(long, help = "运行 Python 在线依赖试算（uv pip compile） / Run python online dependency resolution (uv pip compile)")]
        python: bool,

        #[arg(long, help = "Python 在线检查严格模式（uv 缺失/失败算 error） / Strict python-online check (missing/failure is error)")]
        python_strict: bool,

        #[arg(long, help = "覆盖 Python 在线检查缓存目录 / Override cache dir for python-online check")]
        cache_dir: Option<PathBuf>,
    },

    #[command(about = "解包插件 zip 到插件目录（冲突告警；md5 相同自动跳过） / Unpack plugin zip into plugin dir (warn conflicts; skip identical by md5)")]
    Unpack {
        #[arg(help = "bundle zip 路径 / Bundle zip path")]
        zip_path: PathBuf,

        #[arg(long, help = "仓库根目录（可选，默认自动探测） / Repo root (optional, auto-detect by default)")]
        root: Option<PathBuf>,

        #[arg(long, help = "目标插件目录（默认 <repo_root>/plugin/plugins） / Destination plugin dir (default <repo_root>/plugin/plugins)")]
        dest: Option<PathBuf>,

        #[arg(long, help = "强制覆盖已有文件/插件 / Force overwrite existing plugins/files")]
        force: bool,
    },

    #[command(about = "终端图形界面（支持鼠标/进度条） / Terminal UI (mouse + progress)")]
    Tui {
        #[arg(long, help = "仓库根目录（可选） / Repo root (optional)")]
        root: Option<PathBuf>,
    },
}

fn resolve_zip_path(input: &Path, repo_root: &Path) -> Result<PathBuf> {
    if input.is_absolute() {
        return Ok(input.to_path_buf());
    }

    let cwd = std::env::current_dir().context("failed to get cwd")?;
    let candidates = [
        input.to_path_buf(),
        cwd.join(input),
        repo_root.join(input),
        repo_root
            .join("plugin")
            .join("tool")
            .join("neko_plugin_cli")
            .join(input),
    ];

    for c in &candidates {
        if c.is_file() {
            return Ok(c.to_path_buf());
        }
    }

    anyhow::bail!(
        "zip not found; tried: {}",
        candidates
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(" | ")
    )
}
