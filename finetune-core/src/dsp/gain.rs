/// Rampa di volume esponenziale RT-safe per l'applicazione del gain per-app.
/// Port del comportamento di `ProcessTapController` (rampa esponenziale verso il
/// target per evitare click/pop).
#[derive(Debug, Clone)]
pub struct GainRamp {
    current: f32,
    target: f32,
    /// Coeff di assestamento per frame (0..1). Più alto = più veloce.
    coefficient: f32,
}

impl Default for GainRamp {
    fn default() -> Self {
        GainRamp::new(1.0, 1.0, 0.001)
    }
}

impl GainRamp {
    pub fn new(current: f32, target: f32, coefficient: f32) -> Self {
        GainRamp {
            current,
            target,
            coefficient: coefficient.clamp(0.0, 1.0),
        }
    }

    /// Imposta il target. RT-safe.
    #[inline]
    pub fn set_target(&mut self, target: f32) {
        self.target = target;
    }

    pub fn target(&self) -> f32 {
        self.target
    }

    pub fn current(&self) -> f32 {
        self.current
    }

    #[inline]
    pub fn is_settled(&self) -> bool {
        (self.current - self.target).abs() < 1e-4
    }

    /// Fa avanzare la rampa di un frame e restituisce il gain corrente.
    #[inline]
    pub fn advance(&mut self) -> f32 {
        if !self.is_settled() {
            self.current += (self.target - self.current) * self.coefficient;
        }
        self.current
    }

    /// Converte una frequenza target di click-free (es. 40 ms a 48 kHz → ~1920 frame).
    pub fn coefficient_for_time_constant(frames_constant: f32) -> f32 {
        // 1 - exp(-1/N)
        1.0 - (-1.0 / frames_constant.max(1.0)).exp()
    }
}

/// Gate di uscita con fade-in half-cosine (port di `advanceOutputGate` di FineTune).
/// Fasi: 0 armed (muto), 1 ramping (fade-in), 2 open. Si ri-arma dopo silenzio.
#[derive(Debug, Clone)]
pub struct OutputGate {
    phase: u8,
    ramp_frames: u32,
    frames_at_current: u32,
    silence_frames: u32,
    silence_arm_threshold: u32,
    half_cosine: f32,
}

impl OutputGate {
    pub fn new(ramp_frames: u32, silence_arm_threshold: u32) -> Self {
        OutputGate {
            phase: 0,
            ramp_frames: ramp_frames.max(1),
            frames_at_current: 0,
            silence_frames: 0,
            silence_arm_threshold,
            half_cosine: 0.0,
        }
    }

    /// Avanza il gate per un frame dato il livello di ingresso.
    /// Restituisce il moltiplicatore (0..=1).
    #[inline]
    pub fn advance(&mut self, input_level: f32) -> f32 {
        const SILENCE: f32 = 1e-4;
        if input_level <= SILENCE {
            self.silence_frames += 1;
            if self.silence_frames >= self.silence_arm_threshold {
                self.phase = 0;
                self.frames_at_current = 0;
                self.half_cosine = 0.0;
            }
        } else {
            self.silence_frames = 0;
            match self.phase {
                0 => {
                    self.phase = 1;
                    self.frames_at_current = 0;
                }
                1 => {
                    self.frames_at_current += 1;
                    let t = (self.frames_at_current as f32 / self.ramp_frames as f32).min(1.0);
                    self.half_cosine = 0.5 - 0.5 * (std::f32::consts::PI * t).cos();
                    if self.frames_at_current >= self.ramp_frames {
                        self.phase = 2;
                    }
                    return self.half_cosine;
                }
                _ => return 1.0,
            }
        }
        0.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ramp_converges_to_target() {
        let mut ramp = GainRamp::new(0.0, 1.0, 0.01);
        let mut steps = 0;
        while !ramp.is_settled() && steps < 100_000 {
            ramp.advance();
            steps += 1;
        }
        assert!(ramp.is_settled());
        assert!(ramp.current() > 0.995, "current={}", ramp.current());
        assert!(steps < 100_000, "did not converge in {steps} steps");
    }

    #[test]
    fn ramp_starts_ranged() {
        let mut ramp = GainRamp::new(0.5, 0.1, 0.5);
        let first = ramp.advance();
        assert!(first < 0.5 && first > 0.1);
    }

    #[test]
    fn coefficient_time_constant_sane() {
        let c = GainRamp::coefficient_for_time_constant(1920.0);
        assert!(c > 0.0 && c < 0.01);
    }

    #[test]
    fn gate_opens_and_rearms() {
        let mut gate = OutputGate::new(10, 5);
        // Sigillato con audio: il primo frame è la fase di arming (muto),
        // poi il gate fa fade-in fino ad aprirsi.
        let mut opened = false;
        for _ in 0..10 {
            let m = gate.advance(0.5);
            opened |= m > 0.0;
        }
        assert!(opened, "gate should fade in");
        assert_eq!(gate.advance(0.5), 1.0);
        // Silenzio → re-arm
        for _ in 0..5 {
            let m = gate.advance(0.0);
            assert_eq!(m, 0.0);
        }
        // Riaprirsi da zero (fade-in, non ancora open)
        let m = gate.advance(0.5);
        assert!(m >= 0.0 && m < 1.0, "should fade in, got {m}");
    }
}