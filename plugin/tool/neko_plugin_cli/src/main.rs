mod cli;
mod core;
mod tui;

fn main() {
    if let Err(e) = cli::run() {
        eprintln!("{e:#}");
        std::process::exit(1);
    }
}
