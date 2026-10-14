/* FineTune for Linux — DSP building blocks.
 *
 * Ports of the macOS FineTune processors:
 *   BiquadMath (RBJ cookbook), BiquadProcessor (stereo cascade),
 *   EQProcessor (10-band graphic EQ), AutoEQProcessor (parametric + preamp),
 *   LoudnessCompensator (4-section ISO 226 fit — gains computed in Python),
 *   LoudnessEqualizer (K-weighting + RMS detector + gain computer + smoother),
 *   SoftLimiter (asymptotic soft knee).
 *
 * Everything here is real-time safe: no allocation, no locks, no I/O.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#ifndef FINETUNE_DSP_H
#define FINETUNE_DSP_H

#include <math.h>
#include <stdbool.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define FT_MAX_SECTIONS 10
#define FT_EQ_BANDS 10
#define FT_LC_BANDS 4

enum ft_filter_type { FT_PEAKING = 0, FT_LOWSHELF = 1, FT_HIGHSHELF = 2, FT_HIGHPASS = 3 };

struct ft_filter_spec {
	int type;
	double freq;
	double gain_db;
	double q;
};

struct ft_coef { double b0, b1, b2, a1, a2; };

/* Stereo biquad cascade, transposed direct form II, double precision state. */
struct ft_cascade {
	int n;
	struct ft_coef c[FT_MAX_SECTIONS];
	double z[FT_MAX_SECTIONS][2][2]; /* [section][channel][z1,z2] */
};

static inline struct ft_coef ft_unity(void)
{
	struct ft_coef c = { 1.0, 0.0, 0.0, 0.0, 0.0 };
	return c;
}

static inline struct ft_coef ft_peaking(double f, double gain_db, double q, double sr)
{
	double A = pow(10.0, gain_db / 40.0);
	double w = 2.0 * M_PI * f / sr;
	double sn = sin(w), cs = cos(w);
	double alpha = sn / (2.0 * q);
	double a0 = 1.0 + alpha / A;
	struct ft_coef c = {
		(1.0 + alpha * A) / a0,
		(-2.0 * cs) / a0,
		(1.0 - alpha * A) / a0,
		(-2.0 * cs) / a0,
		(1.0 - alpha / A) / a0,
	};
	return c;
}

static inline struct ft_coef ft_lowshelf(double f, double gain_db, double q, double sr)
{
	double A = pow(10.0, gain_db / 40.0);
	double w = 2.0 * M_PI * f / sr;
	double sn = sin(w), cs = cos(w);
	double alpha = sn / (2.0 * q);
	double t = 2.0 * sqrt(A) * alpha;
	double a0 = (A + 1.0) + (A - 1.0) * cs + t;
	struct ft_coef c = {
		A * ((A + 1.0) - (A - 1.0) * cs + t) / a0,
		2.0 * A * ((A - 1.0) - (A + 1.0) * cs) / a0,
		A * ((A + 1.0) - (A - 1.0) * cs - t) / a0,
		-2.0 * ((A - 1.0) + (A + 1.0) * cs) / a0,
		((A + 1.0) + (A - 1.0) * cs - t) / a0,
	};
	return c;
}

static inline struct ft_coef ft_highshelf(double f, double gain_db, double q, double sr)
{
	double A = pow(10.0, gain_db / 40.0);
	double w = 2.0 * M_PI * f / sr;
	double sn = sin(w), cs = cos(w);
	double alpha = sn / (2.0 * q);
	double t = 2.0 * sqrt(A) * alpha;
	double a0 = (A + 1.0) - (A - 1.0) * cs + t;
	struct ft_coef c = {
		A * ((A + 1.0) + (A - 1.0) * cs + t) / a0,
		-2.0 * A * ((A - 1.0) + (A + 1.0) * cs) / a0,
		A * ((A + 1.0) + (A - 1.0) * cs - t) / a0,
		2.0 * ((A - 1.0) - (A + 1.0) * cs) / a0,
		((A + 1.0) - (A - 1.0) * cs - t) / a0,
	};
	return c;
}

static inline struct ft_coef ft_highpass(double f, double q, double sr)
{
	double w = 2.0 * M_PI * f / sr;
	double sn = sin(w), cs = cos(w);
	double alpha = sn / (2.0 * q);
	double a0 = 1.0 + alpha;
	struct ft_coef c = {
		((1.0 + cs) / 2.0) / a0,
		(-(1.0 + cs)) / a0,
		((1.0 + cs) / 2.0) / a0,
		(-2.0 * cs) / a0,
		(1.0 - alpha) / a0,
	};
	return c;
}

/* Inverse bilinear (digital->analog at source rate) then forward at target rate. */
static inline double ft_prewarp(double f, double src_rate, double dst_rate)
{
	double fa = (src_rate / M_PI) * tan(M_PI * f / src_rate);
	return (dst_rate / M_PI) * atan(M_PI * fa / dst_rate);
}

static inline struct ft_coef ft_coef_for(const struct ft_filter_spec *s, double sr, double optimized_rate)
{
	double f = s->freq;
	if (optimized_rate > 0 && fabs(optimized_rate - sr) > 1.0)
		f = ft_prewarp(f, optimized_rate, sr);
	/* Above-Nyquist or invalid bands are bypassed: omega > pi yields unstable poles. */
	if (!(f > 0.0) || f >= sr / 2.0 || !(s->q > 0.0) || !isfinite(s->gain_db))
		return ft_unity();
	switch (s->type) {
	case FT_LOWSHELF: return ft_lowshelf(f, s->gain_db, s->q, sr);
	case FT_HIGHSHELF: return ft_highshelf(f, s->gain_db, s->q, sr);
	case FT_HIGHPASS: return ft_highpass(f, s->q, sr);
	default: return ft_peaking(f, s->gain_db, s->q, sr);
	}
}

/* Rebuild coefficients. Delay state is kept so coefficient changes are click-free
 * (same behaviour as the macOS processors, which never reset on settings change). */
static inline void ft_cascade_set(struct ft_cascade *c, const struct ft_filter_spec *specs, int n,
				  double sr, double optimized_rate)
{
	if (n > FT_MAX_SECTIONS)
		n = FT_MAX_SECTIONS;
	if (n > c->n)
		for (int i = c->n; i < n; i++)
			memset(c->z[i], 0, sizeof(c->z[i]));
	for (int i = 0; i < n; i++)
		c->c[i] = ft_coef_for(&specs[i], sr, optimized_rate);
	c->n = n;
}

static inline void ft_cascade_reset(struct ft_cascade *c)
{
	memset(c->z, 0, sizeof(c->z));
}

/* Interleaved stereo, in place. */
static inline void ft_cascade_process(struct ft_cascade *c, float *buf, unsigned frames)
{
	for (int s = 0; s < c->n; s++) {
		const struct ft_coef k = c->c[s];
		for (int ch = 0; ch < 2; ch++) {
			double z1 = c->z[s][ch][0], z2 = c->z[s][ch][1];
			float *p = buf + ch;
			for (unsigned i = 0; i < frames; i++, p += 2) {
				double x = *p;
				double y = k.b0 * x + z1;
				z1 = k.b1 * x - k.a1 * y + z2;
				z2 = k.b2 * x - k.a2 * y;
				*p = (float)y;
			}
			c->z[s][ch][0] = z1;
			c->z[s][ch][1] = z2;
		}
	}
	/* NaN safety net — pathological coefficients must not poison the chain. */
	if (frames > 0 && (isnan(buf[0]) || isnan(buf[1]) || isnan(buf[frames * 2 - 1]))) {
		ft_cascade_reset(c);
		memset(buf, 0, frames * 2 * sizeof(float));
	}
}

/* ---------------- Soft limiter ---------------- */

#define FT_LIMIT_THRESHOLD 0.95f
#define FT_LIMIT_CEILING 1.0f

static inline float ft_soft_limit(float x)
{
	float a = fabsf(x);
	if (a <= FT_LIMIT_THRESHOLD)
		return x;
	const float head = FT_LIMIT_CEILING - FT_LIMIT_THRESHOLD;
	float over = a - FT_LIMIT_THRESHOLD;
	float y = FT_LIMIT_THRESHOLD + head * (over / (over + head));
	return x >= 0 ? y : -y;
}

static inline void ft_soft_limit_buffer(float *buf, unsigned samples)
{
	float peak = 0.0f;
	for (unsigned i = 0; i < samples; i++) {
		float a = fabsf(buf[i]);
		if (a > peak)
			peak = a;
	}
	if (!(peak > FT_LIMIT_THRESHOLD))
		return;
	for (unsigned i = 0; i < samples; i++)
		buf[i] = ft_soft_limit(buf[i]);
}

/* ---------------- Loudness equalizer (wideband leveler) ---------------- */

struct ft_leveler_settings {
	float target_db, max_boost_db, max_cut_db;
	float comp_threshold_offset_db, comp_ratio, comp_knee_db;
	float window_ms, hop_ms;
	float det_attack_ms, det_release_ms;
	float gain_attack_ms, gain_release_ms;
	float noise_floor_db, low_level_max_boost_db;
};

static inline struct ft_leveler_settings ft_leveler_defaults(void)
{
	struct ft_leveler_settings s = {
		-12.f, 6.f, 4.f, 6.f, 1.6f, 8.f, 400.f, 100.f, 25.f, 600.f, 250.f, 3000.f, -40.f, 0.5f,
	};
	return s;
}

#define FT_LEVELER_MAX_WINDOW (192000 / 2) /* 500 ms @ 192 kHz */

struct ft_leveler {
	bool enabled;
	struct ft_leveler_settings s;
	struct ft_coef k1, k2;
	double k1z[2], k2z[2];
	float ring[FT_LEVELER_MAX_WINDOW];
	int window, hop, widx, hopc;
	double sum;
	float det_attack, det_release, gain_attack, gain_release;
	float smoothed_level_db, gain_db, lin_gain;
};

static inline float ft_tc(float time_ms, float step_ms)
{
	return 1.0f - expf(-step_ms / fmaxf(time_ms, 1e-6f));
}

static inline void ft_leveler_init(struct ft_leveler *l, double sr, bool enabled)
{
	l->enabled = enabled;
	l->s = ft_leveler_defaults();
	l->k1 = ft_highshelf(1500.0, 4.0, 1.0 / sqrt(2.0), sr);
	l->k2 = ft_highpass(38.0, 1.0 / sqrt(2.0), sr);
	memset(l->k1z, 0, sizeof(l->k1z));
	memset(l->k2z, 0, sizeof(l->k2z));
	int w = (int)(l->s.window_ms / 1000.f * sr);
	if (w < 1)
		w = 1;
	if (w > FT_LEVELER_MAX_WINDOW)
		w = FT_LEVELER_MAX_WINDOW;
	l->window = w;
	int h = (int)(l->s.hop_ms / 1000.f * sr);
	l->hop = h < 1 ? 1 : h;
	memset(l->ring, 0, sizeof(float) * (size_t)w);
	l->widx = 0;
	l->hopc = 0;
	l->sum = 0;
	l->det_attack = ft_tc(l->s.det_attack_ms, l->s.hop_ms);
	l->det_release = ft_tc(l->s.det_release_ms, l->s.hop_ms);
	l->gain_attack = ft_tc(l->s.gain_attack_ms, l->s.hop_ms);
	l->gain_release = ft_tc(l->s.gain_release_ms, l->s.hop_ms);
	l->smoothed_level_db = -120.f;
	l->gain_db = 0.f;
	l->lin_gain = 1.f;
}

static inline float ft_clampf(float v, float lo, float hi)
{
	return v < lo ? lo : (v > hi ? hi : v);
}

static inline float ft_leveler_desired_gain(const struct ft_leveler_settings *s, float level)
{
	/* Upward boost with noise-floor protection */
	float boost = ft_clampf(s->target_db - level, 0.f, s->max_boost_db);
	if (level < s->noise_floor_db && boost > 0)
		boost = fminf(boost, s->low_level_max_boost_db);
	/* Gentle soft-knee cut */
	float cut = 0.f;
	if (s->max_cut_db > 0) {
		float thr = s->target_db + s->comp_threshold_offset_db;
		float ratio = fmaxf(s->comp_ratio, 1.f);
		float knee = fmaxf(s->comp_knee_db, 0.f);
		float out = level;
		if (ratio > 1.f) {
			if (knee <= 0) {
				if (level > thr)
					out = thr + (level - thr) / ratio;
			} else {
				float ks = thr - knee * 0.5f, ke = thr + knee * 0.5f;
				if (level >= ke)
					out = thr + (level - thr) / ratio;
				else if (level > ks) {
					float d = level - ks;
					out = level + (1.f / ratio - 1.f) * d * d / (2.f * knee);
				}
			}
		}
		cut = ft_clampf(out - level, -s->max_cut_db, 0.f);
	}
	return boost + cut;
}

static inline double ft_biquad_tick(const struct ft_coef *k, double *z, double x)
{
	double y = k->b0 * x + z[0];
	z[0] = k->b1 * x - k->a1 * y + z[1];
	z[1] = k->b2 * x - k->a2 * y;
	return y;
}

static inline void ft_leveler_process(struct ft_leveler *l, float *buf, unsigned frames)
{
	if (!l->enabled)
		return;
	float g = l->lin_gain;
	for (unsigned f = 0; f < frames; f++) {
		float *p = buf + f * 2;
		double mono = (p[0] + p[1]) * 0.5;
		double w = ft_biquad_tick(&l->k2, l->k2z, ft_biquad_tick(&l->k1, l->k1z, mono));
		float sq = (float)(w * w);
		l->sum -= l->ring[l->widx];
		l->ring[l->widx] = sq;
		l->sum += sq;
		if (++l->widx == l->window)
			l->widx = 0;
		if (++l->hopc >= l->hop) {
			l->hopc = 0;
			double ms = l->sum / l->window;
			float level_db = 10.f * log10f(fmaxf((float)ms, 1e-12f));
			float c = level_db > l->smoothed_level_db ? l->det_attack : l->det_release;
			l->smoothed_level_db += c * (level_db - l->smoothed_level_db);
			float target = ft_leveler_desired_gain(&l->s, l->smoothed_level_db);
			float gc = target < l->gain_db ? l->gain_attack : l->gain_release;
			l->gain_db += gc * (target - l->gain_db);
			g = powf(10.f, l->gain_db / 20.f);
			l->lin_gain = g;
		}
		p[0] *= g;
		p[1] *= g;
	}
}

#endif
