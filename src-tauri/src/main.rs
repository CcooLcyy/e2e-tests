#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod shell;
mod update;
fn main() {
    shell::run();
}
