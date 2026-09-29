// FineTune Linux — icone vettoriali disegnate con egui (stile SF Symbols).

use eframe::egui::{Align2, Color32, Pos2, Rect, Shape, Stroke, Ui, Vec2};

fn arc_points(center: Pos2, r: f32, from_deg: f32, to_deg: f32, segs: usize) -> Vec<Pos2> {
    let mut out = Vec::with_capacity(segs + 1);
    for i in 0..=segs {
        let t = i as f32 / segs as f32;
        let ang = (from_deg + (to_deg - from_deg) * t).to_radians();
        out.push(center + Vec2::new(r * ang.cos(), -r * ang.sin()));
    }
    out
}

fn speaker_body(ui: &Ui, rect: Rect, color: Color32) {
    let p = ui.painter();
    let s = rect.width().min(rect.height());
    let body_w = s * 0.34;
    let cone_h = s * 0.42;
    let stroke = Stroke::new(s * 0.10, color);
    let fill = s * 0.055;
    let cx = rect.center().x;
    let cy = rect.center().y;

    // corpo: rettangolo arrotondato
    let body_rect = Rect::from_min_size(
        Pos2::new(cx - body_w, cy - cone_h),
        Vec2::new(body_w, cone_h * 2.0),
    );
    p.add(Shape::rect_filled(body_rect, fill, color));
    // cono: triangolo verso destra
    let tip_x = cx + s * 0.06;
    p.add(Shape::closed_line(
        vec![
            Pos2::new(cx + body_w - fill, cy - cone_h),
            Pos2::new(tip_x, cy),
            Pos2::new(cx + body_w - fill, cy + cone_h),
        ],
        Stroke::new(fill, color),
    ));
    let _ = stroke;
}

fn speaker_waves(ui: &Ui, rect: Rect, n: u32, color: Color32) {
    let p = ui.painter();
    let s = rect.width().min(rect.height());
    let stroke = Stroke::new(s * 0.09, color);
    let mut cx = rect.center().x + s * 0.16;
    let cy = rect.center().y;
    let mut r = s * 0.22;
    for _ in 0..n {
        p.add(Shape::line(
            arc_points(Pos2::new(cx, cy), r, -55.0, 55.0, 8),
            stroke,
        ));
        r += s * 0.16;
        cx += s * 0.0;
    }
}

fn speaker_slash(ui: &Ui, rect: Rect, color: Color32) {
    let p = ui.painter();
    let s = rect.width().min(rect.height());
    speaker_body(ui, rect, color);
    let stroke = Stroke::new(s * 0.11, color);
    let p0 = Pos2::new(rect.left(), rect.top());
    let p1 = Pos2::new(rect.right(), rect.bottom());
    p.add(Shape::line(vec![p0, p1], stroke));
    p.add(Shape::line(
        arc_points(
            Pos2::new(rect.center().x + s * 0.05, rect.center().y),
            s * 0.36,
            -60.0,
            -20.0,
            4,
        ),
        Stroke::new(s * 0.09, color),
    ));
}

pub struct IconDrawer<'a> {
    pub ui: &'a Ui,
}

impl<'a> IconDrawer<'a> {
    pub fn new(ui: &'a Ui) -> Self {
        Self { ui }
    }

    pub fn speaker(&self, rect: Rect, level: f32, color: Color32) {
        speaker_body(self.ui, rect, color);
        let n = if level <= 0.0 {
            0
        } else if level < 0.34 {
            1
        } else if level < 0.67 {
            2
        } else {
            3
        };
        speaker_waves(self.ui, rect, n, color);
    }

    pub fn speaker_muted(&self, rect: Rect, color: Color32) {
        speaker_slash(self.ui, rect, color);
    }

    pub fn mic(&self, rect: Rect, muted: bool, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let stroke = Stroke::new(s * 0.10, color);
        let cx = rect.center().x;
        let cy = rect.center().y;
        // capsula
        let capsule = Rect::from_center_size(
            Pos2::new(cx, cy - s * 0.10),
            Vec2::new(s * 0.26, s * 0.42),
        );
        p.add(Shape::rect_filled(capsule, s * 0.13, color));
        // staffa
        let a = s * 0.60;
        p.add(Shape::line(
            vec![
                Pos2::new(cx - a, cy + s * 0.20),
                Pos2::new(cx + a, cy + s * 0.20),
            ],
            stroke,
        ));
        // codolo
        p.add(Shape::line(
            vec![
                Pos2::new(cx, cy + s * 0.20),
                Pos2::new(cx, cy + s * 0.46),
            ],
            Stroke::new(s * 0.12, color),
        ));
        if muted {
            let p0 = Pos2::new(cx - a, cy - s * 0.22);
            let p1 = Pos2::new(cx + a, cy + s * 0.22);
            p.add(Shape::line(vec![p0, p1], stroke));
        }
    }

    pub fn gear(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let c = rect.center();
        let s = rect.width().min(rect.height());
        let r0 = s * 0.19;
        let r1 = s * 0.36;
        let mut pts = Vec::new();
        let teeth = 8;
        for i in 0..teeth * 2 {
            let ang = (i as f32 / (teeth * 2) as f32 * std::f32::consts::TAU).to_radians();
            let rr = if i % 2 == 0 { r0 } else { r1 };
            pts.push(c + Vec2::new(rr * ang.cos(), rr * ang.sin()));
        }
        p.add(Shape::closed_line(pts, Stroke::new(s * 0.06, color)));
        p.circle_filled(c, s * 0.07, color);
    }

    pub fn pencil(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let stroke = Stroke::new(s * 0.09, color);
        let p0 = Pos2::new(rect.left() + s * 0.12, rect.top() + s * 0.28);
        let p1 = Pos2::new(rect.right() - s * 0.18, rect.bottom() - s * 0.08);
        let dir = (p1 - p0).normalized();
        p.add(Shape::line(vec![p0, p1], stroke));
        // punta
        let tip = p1 + dir * s * 0.10;
        p.add(Shape::line(
            vec![p1, Pos2::new(tip.x - s * 0.02, tip.y + s * 0.02)],
            stroke,
        ));
    }

    pub fn check(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let stroke = Stroke::new(s * 0.12, color);
        p.add(Shape::line(
            vec![
                Pos2::new(rect.left() + s * 0.10, rect.center().y + s * 0.02),
                Pos2::new(rect.center().x - s * 0.08, rect.bottom() - s * 0.10),
            ],
            stroke,
        ));
        p.add(Shape::line(
            vec![
                Pos2::new(rect.center().x - s * 0.08, rect.bottom() - s * 0.10),
                Pos2::new(rect.right() - s * 0.06, rect.top() + s * 0.12),
            ],
            stroke,
        ));
    }

    pub fn chevron(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let stroke = Stroke::new(s * 0.16, color);
        let c = rect.center();
        p.add(Shape::line(
            vec![
                Pos2::new(c.x - s * 0.12, c.y - s * 0.18),
                Pos2::new(c.x + s * 0.10, c.y),
            ],
            stroke,
        ));
        p.add(Shape::line(
            vec![
                Pos2::new(c.x + s * 0.10, c.y),
                Pos2::new(c.x - s * 0.12, c.y + s * 0.18),
            ],
            stroke,
        ));
    }

    pub fn globe(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let c = rect.center();
        let stroke = Stroke::new(s * 0.08, color);
        p.add(Shape::circle_stroke(c, s * 0.34, stroke));
        // meridiani
        for (vx, vy) in [(-0.34f32, 0.0f32), (0.34, 0.0)] {
            let mut pts = Vec::new();
            for i in 0..=16 {
                let t = i as f32 / 16.0;
                let ang = t * std::f32::consts::PI;
                pts.push(c + Vec2::new(vx * ang.sin(), vy * 0.0 + ang.cos() * 0.34 * s * 0.4));
            }
            p.add(Shape::line(pts, Stroke::new(s * 0.05, color)));
        }
        p.add(Shape::line(
            vec![
                Pos2::new(c.x - s * 0.34, c.y),
                Pos2::new(c.x + s * 0.34, c.y),
            ],
            Stroke::new(s * 0.05, color),
        ));
    }

    pub fn equalizer(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let n = 3;
        for i in 0..n {
            let total = n as f32;
            let x_center = rect.left() + rect.width() * (i as f32 + 0.5) / total;
            let x0 = x_center - s * 0.05;
            let x1 = x_center + s * 0.05;
            let top = rect.top() + s * 0.12;
            let bottom = rect.bottom() - s * 0.12;
            let h = match i {
                0 => 0.55,
                1 => 0.30,
                _ => 0.72,
            };
            let y_top = top + (bottom - top) * (1.0 - h);
            p.add(Shape::line(
                vec![Pos2::new(x0, top), Pos2::new(x0, y_top)],
                Stroke::new(s * 0.10, color),
            ));
            p.add(Shape::line(
                vec![Pos2::new(x1, y_top), Pos2::new(x1, bottom)],
                Stroke::new(s * 0.10, color),
            ));
            p.circle_filled(Pos2::new(x_center, y_top), s * 0.07, color);
        }
    }

    pub fn xmark(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let stroke = Stroke::new(s * 0.11, color);
        p.add(Shape::line(
            vec![
                Pos2::new(rect.left() + s * 0.14, rect.top() + s * 0.14),
                Pos2::new(rect.right() - s * 0.14, rect.bottom() - s * 0.14),
            ],
            stroke,
        ));
        p.add(Shape::line(
            vec![
                Pos2::new(rect.left() + s * 0.14, rect.bottom() - s * 0.14),
                Pos2::new(rect.right() - s * 0.14, rect.top() + s * 0.14),
            ],
            stroke,
        ));
    }

    pub fn heart(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let c = rect.center();
        let path = vec![
            Pos2::new(c.x, c.y + s * 0.34),
            Pos2::new(c.x - s * 0.38, c.y - s * 0.02),
            Pos2::new(c.x - s * 0.38, c.y - s * 0.24),
            Pos2::new(c.x - s * 0.18, c.y - s * 0.34),
            Pos2::new(c.x, c.y - s * 0.18),
            Pos2::new(c.x + s * 0.18, c.y - s * 0.34),
            Pos2::new(c.x + s * 0.38, c.y - s * 0.24),
            Pos2::new(c.x + s * 0.38, c.y - s * 0.02),
        ];
        p.add(Shape::closed_line(path, Stroke::new(s * 0.08, color)));
    }

    pub fn waveform(&self, rect: Rect, color: Color32) {
        let p = self.ui.painter();
        let s = rect.width().min(rect.height());
        let n = 12;
        for i in 0..n {
            let t = i as f32 / (n as f32 - 1.0);
            let x = rect.left() + rect.width() * t;
            let h = (0.5 - (t - 0.5).abs() * 2.0).max(0.15);
            let half = s * 0.30 * h;
            p.add(Shape::line(
                vec![
                    Pos2::new(x, rect.center().y - half),
                    Pos2::new(x, rect.center().y + half),
                ],
                Stroke::new(s * 0.07, color),
            ));
        }
    }

    pub fn filter_slant(&self, rect: Rect, color: Color32) {
        // slider.vertical.3 alternativo (equalizzatore a 3 sliders)
        self.equalizer(rect, color);
    }

    pub fn text_center(&self, rect: Rect, text: &str, color: Color32, size: f32) {
        self.ui
            .painter()
            .text(rect.center(), Align2::CENTER_CENTER, text, eframe::egui::FontId::proportional(size), color);
    }
}