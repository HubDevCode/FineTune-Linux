// FineTune Linux — design tokens (port da Views/DesignSystem/DesignTokens.swift).

use eframe::egui::{Color32, CornerRadius, FontId, Margin, Stroke};

/// Colori dinamici a seconda del tema (light/dark).
#[derive(Clone, Copy)]
pub struct Tokens {
    // Text
    pub text_primary: Color32,
    pub text_secondary: Color32,
    pub text_tertiary: Color32,
    pub text_quaternary: Color32,
    // Interattivi
    pub hover_surface: Color32,
    pub interactive_default: Color32,
    pub interactive_hover: Color32,
    pub interactive_active: Color32,
    pub accent: Color32,
    pub muted_indicator: Color32,
    pub green: Color32,
    // Superfici
    pub window_bg: Color32,
    pub glass_fill: Color32,
    pub glass_fill_strong: Color32,
    pub glass_row_border: Color32,
    pub glass_row_border_hover: Color32,
    pub recessed: Color32,
    pub section_header_text: Color32,
    // Slider
    pub slider_track: Color32,
    pub unity_marker: Color32,
    // Badge device
    pub badge_mono_fill: Color32,
    pub badge_mono_fg: Color32,
    // VU
    pub vu_green: Color32,
    pub vu_yellow: Color32,
    pub vu_orange: Color32,
    pub vu_red: Color32,
    pub vu_unlit: Color32,
    pub vu_muted: Color32,
}

impl Tokens {
    pub fn new(dark: bool) -> Self {
        if dark {
            Tokens {
                text_primary: Color32::WHITE,
                text_secondary: Color32::from_gray(200),
                text_tertiary: Color32::from_gray(130),
                text_quaternary: Color32::from_gray(90),
                hover_surface: Color32::from_white_alpha(46),
                interactive_default: Color32::from_white_alpha(150),
                interactive_hover: Color32::from_white_alpha(200),
                interactive_active: Color32::WHITE,
                accent: Color32::from_rgb(58, 130, 246),
                muted_indicator: Color32::from_rgba_premultiplied(237, 80, 80, 217),
                green: Color32::from_rgb(46, 160, 67),
                window_bg: Color32::from_rgb(22, 22, 24),
                glass_fill: Color32::from_white_alpha(22),
                glass_fill_strong: Color32::from_white_alpha(26),
                glass_row_border: Color32::from_white_alpha(22),
                glass_row_border_hover: Color32::from_white_alpha(38),
                recessed: Color32::from_black_alpha(76),
                section_header_text: Color32::from_white_alpha(100),
                slider_track: Color32::from_white_alpha(38),
                unity_marker: Color32::from_white_alpha(130),
                badge_mono_fill: Color32::from_white_alpha(26),
                badge_mono_fg: Color32::from_white_alpha(180),
                vu_green: Color32::from_rgb(52, 199, 102),
                vu_yellow: Color32::from_rgb(242, 192, 51),
                vu_orange: Color32::from_rgb(242, 128, 51),
                vu_red: Color32::from_rgb(230, 64, 64),
                vu_unlit: Color32::from_white_alpha(38),
                vu_muted: Color32::from_white_alpha(90),
            }
        } else {
            Tokens {
                text_primary: Color32::BLACK,
                text_secondary: Color32::from_gray(90),
                text_tertiary: Color32::from_gray(130),
                text_quaternary: Color32::from_gray(170),
                hover_surface: Color32::from_black_alpha(36),
                interactive_default: Color32::from_black_alpha(180),
                interactive_hover: Color32::from_black_alpha(230),
                interactive_active: Color32::BLACK,
                accent: Color32::from_rgb(10, 105, 220),
                muted_indicator: Color32::from_rgba_premultiplied(200, 40, 40, 217),
                green: Color32::from_rgb(30, 130, 65),
                window_bg: Color32::from_rgb(242, 242, 247),
                glass_fill: Color32::from_white_alpha(120),
                glass_fill_strong: Color32::from_white_alpha(220),
                glass_row_border: Color32::from_black_alpha(18),
                glass_row_border_hover: Color32::from_black_alpha(26),
                recessed: Color32::from_black_alpha(10),
                section_header_text: Color32::from_black_alpha(170),
                slider_track: Color32::from_black_alpha(38),
                unity_marker: Color32::from_black_alpha(130),
                badge_mono_fill: Color32::from_black_alpha(26),
                badge_mono_fg: Color32::from_black_alpha(170),
                vu_green: Color32::from_rgb(46, 180, 92),
                vu_yellow: Color32::from_rgb(220, 175, 46),
                vu_orange: Color32::from_rgb(220, 115, 46),
                vu_red: Color32::from_rgb(205, 57, 57),
                vu_unlit: Color32::from_black_alpha(38),
                vu_muted: Color32::from_black_alpha(90),
            }
        }
    }
}

/// Spaziature (xxs..xxl).
pub const S_XXS: f32 = 2.0;
pub const S_XS: f32 = 4.0;
pub const S_SM: f32 = 8.0;
pub const S_MD: f32 = 12.0;
pub const S_LG: f32 = 16.0;
pub const S_XL: f32 = 20.0;
pub const S_XXL: f32 = 24.0;

/// Dimensioni popup.
pub const POPUP_WIDTH: f32 = 510.0;
pub const CONTENT_PADDING: f32 = 16.0;
pub const CORNER_RADIUS: u8 = 12;
pub const ROW_RADIUS: u8 = 10;
pub const BUTTON_RADIUS: u8 = 6;
pub const ROW_CONTENT_HEIGHT: f32 = 28.0;
pub const SLIDER_WIDTH: f32 = 140.0;
pub const PERCENTAGE_WIDTH: f32 = 40.0;
pub const MAX_SCROLL_HEIGHT: f32 = 420.0;
pub const MIN_SCROLL_HEIGHT: f32 = 160.0;
pub const FOOTER_RESERVE: f32 = 40.0;
pub const MIN_TOUCH: f32 = 16.0;

pub const fn corner() -> CornerRadius {
    CornerRadius::same(CORNER_RADIUS)
}
pub const fn row_corner() -> CornerRadius {
    CornerRadius::same(ROW_RADIUS)
}
pub const fn radius(r: u8) -> CornerRadius {
    CornerRadius::same(r)
}

pub fn frame_margin() -> Margin {
    Margin::symmetric(CONTENT_PADDING as i8, 14)
}

/// Font helper.
pub fn f_row(size: f32) -> FontId {
    FontId::proportional(size)
}
pub fn f_mono(size: f32) -> FontId {
    FontId::monospace(size)
}

pub fn stroke(_t: &Tokens, width: f32, color: Color32) -> Stroke {
    Stroke::new(width, color)
}