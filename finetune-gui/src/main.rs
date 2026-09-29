// FineTune Linux — entrypoint GUI.

mod app;
mod design;
mod icons;
mod settings;
mod widgets;

use eframe::egui;

fn main() -> eframe::Result {
    let app = app::FineTuneApp::new();
    let (width, height) = match app.settings.settings.popup_size.as_str() {
        "spacious" => (560.0, 660.0),
        "compact" => (470.0, 500.0),
        _ => (510.0, 560.0),
    };
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([width, height])
            .with_min_inner_size([width, height])
            .with_max_inner_size([width, height])
            .with_resizable(false)
            .with_title("FineTune"),
        ..Default::default()
    };
    eframe::run_native(
        "FineTune",
        options,
        Box::new(|_cc| Ok(Box::new(app))),
    )
}

fn _noop(_: &egui::Context) {}