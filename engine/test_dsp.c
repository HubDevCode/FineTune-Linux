/* Unit tests for dsp.h (run with `make test`). */
#include <stdio.h>
#include <stdlib.h>
#include "dsp.h"

static int failures;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("FAIL: " __VA_ARGS__); printf("\n"); } } while (0)

static double tone_gain_db(struct ft_cascade *c, double f, double sr)
{
	enum { N = 48000 };
	static float buf[N * 2];
	for (int i = 0; i < N; i++)
		buf[i * 2] = buf[i * 2 + 1] = (float)(0.25 * sin(2 * M_PI * f * i / sr));
	ft_cascade_process(c, buf, N);
	float peak = 0;
	for (int i = N; i < N * 2; i++)
		peak = fmaxf(peak, fabsf(buf[i]));
	return 20 * log10(peak / 0.25);
}

int main(void)
{
	const double sr = 48000;
	struct ft_cascade c = { 0 };
	struct ft_filter_spec flat[10];
	const double freqs[10] = { 31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000 };
	for (int i = 0; i < 10; i++)
		flat[i] = (struct ft_filter_spec){ FT_PEAKING, freqs[i], 0, 1.4 };
	ft_cascade_set(&c, flat, 10, sr, 0);
	CHECK(fabs(tone_gain_db(&c, 1000, sr)) < 0.05, "flat EQ is not unity");

	struct ft_cascade p = { 0 };
	struct ft_filter_spec pk = { FT_PEAKING, 1000, 6, 1.4 };
	ft_cascade_set(&p, &pk, 1, sr, 0);
	double g = tone_gain_db(&p, 1000, sr);
	CHECK(fabs(g - 6) < 0.2, "peaking +6 dB at fc measured %.2f", g);

	struct ft_cascade ls = { 0 };
	struct ft_filter_spec lsc = { FT_LOWSHELF, 105, 7, 0.71 };
	ft_cascade_set(&ls, &lsc, 1, sr, 0);
	g = tone_gain_db(&ls, 30, sr);
	CHECK(fabs(g - 7) < 0.5, "low shelf +7 dB at 30 Hz measured %.2f", g);

	/* above-Nyquist band must be bypassed (unstable otherwise) */
	struct ft_filter_spec bad = { FT_PEAKING, 30000, 12, 1.4 };
	struct ft_coef k = ft_coef_for(&bad, 44100, 0);
	CHECK(k.b0 == 1 && k.a1 == 0, "above-Nyquist filter not bypassed");

	/* prewarp identity */
	CHECK(fabs(ft_prewarp(1000, 48000, 48000) - 1000) < 1e-6, "prewarp identity");

	/* limiter: transparent below threshold, never above ceiling */
	CHECK(ft_soft_limit(0.5f) == 0.5f, "limiter not transparent");
	CHECK(ft_soft_limit(100.f) <= 1.0f && ft_soft_limit(-100.f) >= -1.0f, "limiter exceeds ceiling");
	CHECK(ft_soft_limit(1.2f) > 0.95f, "limiter too aggressive");

	/* leveler boosts a quiet signal (−30 dBFS) toward the target over time */
	struct ft_leveler *lev = calloc(1, sizeof(*lev));
	ft_leveler_init(lev, sr, true);
	enum { M = 4800 };
	float buf[M * 2];
	float out_peak = 0;
	for (int block = 0; block < 100; block++) {
		for (int i = 0; i < M; i++)
			buf[i * 2] = buf[i * 2 + 1] = (float)(0.0316 * sin(2 * M_PI * 1000 * (block * M + i) / sr));
		ft_leveler_process(lev, buf, M);
		if (block == 99)
			for (int i = 0; i < M * 2; i++)
				out_peak = fmaxf(out_peak, fabsf(buf[i]));
	}
	double boost = 20 * log10(out_peak / 0.0316);
	CHECK(boost > 3 && boost <= 6.1, "leveler boost %.2f dB not within (3, 6]", boost);
	free(lev);

	/* NaN safety */
	struct ft_cascade n = { 0 };
	ft_cascade_set(&n, &pk, 1, sr, 0);
	float nanbuf[8] = { NAN, NAN, 0, 0, 0, 0, 0, 0 };
	ft_cascade_process(&n, nanbuf, 4);
	CHECK(nanbuf[0] == 0 && n.z[0][0][0] == 0, "NaN not flushed");

	printf(failures ? "%d DSP test(s) failed\n" : "all DSP tests passed\n", failures);
	return failures ? 1 : 0;
}
