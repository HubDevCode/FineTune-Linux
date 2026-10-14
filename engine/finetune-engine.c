/* FineTune for Linux — audio engine.
 *
 * The macOS FineTune taps every app with a CoreAudio process tap and runs the
 * audio through its own DSP before sending it to one or more output devices.
 * On Linux the same graph is built with PipeWire:
 *
 *   app stream(s) ──► [chain capture: virtual sink "finetune_app_<id>"]
 *                       │ gain/boost ramp → 10-band EQ → loudness equalizer
 *                       │ → loudness compensation → peak meter
 *                       ├──► [output → device A] fade → AutoEQ(A) → device gain → soft limiter
 *                       └──► [output → device B] fade → AutoEQ(B) → device gain → soft limiter
 *
 * All streams of one chain share a node.group, so they are scheduled in the same
 * graph cycle (PipeWire slaves extra devices with adaptive resampling, which is
 * how multi-device output works). Output switches cross-fade with an
 * equal-power curve, like the macOS CrossfadeOrchestrator.
 *
 * The engine is driven by the Python front-end through a line protocol on
 * stdin and reports events on stdout (see PROTOCOL below). When stdin closes
 * the engine exits and PipeWire tears down every node it created, so apps fall
 * back to their normal routing — no orphaned taps are possible.
 *
 * PROTOCOL (one command per line, space separated):
 *   chain <id> <node_name> <description...>   create a per-app chain
 *   drop <id>                                  destroy a chain
 *   outs <id> [target ...]                     set output devices (node names)
 *   gain <id> <linear>                         app gain (volume × boost [× sw device gain])
 *   mute <id> <0|1>
 *   active <id> <0|1>                          pause/resume the chain's streams
 *   eq <id> <0|1> <g1> ... <g10>               10-band graphic EQ gains (dB)
 *   lc <id> <0|1> <g1> <g2> <g3> <g4>          loudness compensation section gains (dB)
 *   lev <id> <0|1>                             loudness equalizer on/off
 *   dev <target> <gain> <mute> <aeq 0|1> <preamp_db> <opt_rate> <n> [<P|L|H> <f> <g> <q>]...
 *   meter <id> <source_node_name>              peak meter on a capture device
 *   unmeter <id>
 *   ping
 *   quit
 * EVENTS:
 *   ready
 *   rate <id> <hz>
 *   lv <id>=<peak> ...                          every 50 ms
 *   state <id> <state> [error]
 *   pong
 *   err <message>
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#define _GNU_SOURCE
#include <errno.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>

#include <pipewire/pipewire.h>
#include <spa/param/audio/format-utils.h>
#include <spa/utils/list.h>
#include <spa/utils/result.h>

#include "dsp.h"

#define MAX_OUTPUTS 8
#define MAX_FRAMES 8192
#define RING_FRAMES 16384
#define FADE_MS 60.0
#define WARMUP_MAX_MS 300.0
/* One-pole ramp coefficient (~30 ms at 48 kHz), same as the macOS tap. */
#define RAMP_COEF 0.0007f

static const double EQ_FREQS[FT_EQ_BANDS] = { 31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000 };
static const double EQ_Q = 1.4;
static const struct ft_filter_spec LC_TOPOLOGY[FT_LC_BANDS] = {
	{ FT_LOWSHELF, 80, 0, 0.707 },
	{ FT_PEAKING, 180, 0, 0.7 },
	{ FT_PEAKING, 3200, 0, 0.7 },
	{ FT_HIGHSHELF, 10000, 0, 0.85 },
};

struct device_params {
	float gain;
	bool mute;
	bool aeq_on;
	float preamp_db;
	double opt_rate;
	int n;
	struct ft_filter_spec f[FT_MAX_SECTIONS];
};

struct device {
	struct spa_list link;
	char target[256];
	struct device_params p;
};

struct engine;
struct chain;

struct output {
	struct spa_list link;           /* main-thread list of all outputs of a chain */
	struct chain *chain;
	char target[256];
	struct pw_stream *stream;
	struct spa_hook listener;

	/* data-thread state */
	float ring[RING_FRAMES * 2];
	uint32_t rd, wr;                /* monotonically increasing frame counters */
	bool primed;
	float fade;                     /* 0..1 progress */
	float fade_step;
	int fade_dir;                   /* +1 in, -1 out, 0 steady */
	bool fade_out_pending;          /* fade out once the replacement output is primed (warm-up) */
	uint32_t pending_frames;        /* frames waited so far; bounded by WARMUP_MAX_MS */
	bool in_chain;                  /* referenced by chain->outs[] */
	bool dead;                      /* faded out, ready to destroy */
	struct device_params dev;
	float cur_dev_gain;
	struct ft_cascade aeq;
	float preamp_lin;
};

struct chain {
	struct spa_list link;
	struct engine *engine;
	char id[64];
	char node_name[160];
	char desc[256];
	struct pw_stream *capture;
	struct spa_hook listener;
	struct spa_list outputs;        /* main thread */
	bool active;

	/* data-thread state */
	uint32_t rate;
	struct output *outs[MAX_OUTPUTS];
	int n_outs;
	float target_gain;
	bool muted;
	float cur_gain;
	bool eq_on;
	struct ft_filter_spec eq_spec[FT_EQ_BANDS];
	struct ft_cascade eq;
	bool lc_on;
	struct ft_filter_spec lc_spec[FT_LC_BANDS];
	struct ft_cascade lc;
	struct ft_leveler lev;
	float scratch[MAX_FRAMES * 2];
	float oscratch[MAX_FRAMES * 2];
	volatile float peak;
};

struct meter {
	struct spa_list link;
	char id[64];
	struct pw_stream *stream;
	struct spa_hook listener;
	volatile float peak;
};

struct engine {
	struct pw_main_loop *loop;
	struct pw_context *context;
	struct pw_core *core;
	struct spa_hook core_listener;
	struct pw_data_loop *data_loop;
	struct spa_list chains;
	struct spa_list devices;
	struct spa_list meters;
	char inbuf[65536];
	size_t inlen;
	struct spa_source *timer;
	int out_seq;
};

static struct engine E;

/* ------------------------------------------------------------------ utils */

static void emit(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void emit(const char *fmt, ...)
{
	va_list ap;
	va_start(ap, fmt);
	vfprintf(stdout, fmt, ap);
	va_end(ap);
	fputc('\n', stdout);
	fflush(stdout);
}

static struct chain *find_chain(const char *id)
{
	struct chain *c;
	spa_list_for_each(c, &E.chains, link)
		if (strcmp(c->id, id) == 0)
			return c;
	return NULL;
}

static struct device *find_device(const char *target)
{
	struct device *d;
	spa_list_for_each(d, &E.devices, link)
		if (strcmp(d->target, target) == 0)
			return d;
	return NULL;
}

static struct device_params default_device_params(void)
{
	struct device_params p;
	memset(&p, 0, sizeof(p));
	p.gain = 1.0f;
	p.opt_rate = 48000;
	return p;
}

/* Run fn on the data (real-time) thread and wait for it. All mutation of
 * data-thread state goes through here, so the process callbacks never race. */
typedef int (*rt_fn)(struct spa_loop *loop, bool async, uint32_t seq, const void *data, size_t size, void *user);

static void run_rt(rt_fn fn, const void *data, size_t size, void *user)
{
	pw_data_loop_invoke(E.data_loop, fn, 0, data, size, true, user);
}

static const struct spa_pod *build_format(struct spa_pod_builder *b)
{
	struct spa_audio_info_raw info;
	memset(&info, 0, sizeof(info));
	info.format = SPA_AUDIO_FORMAT_F32;
	info.channels = 2;
	info.position[0] = SPA_AUDIO_CHANNEL_FL;
	info.position[1] = SPA_AUDIO_CHANNEL_FR;
	return spa_format_audio_raw_build(b, SPA_PARAM_EnumFormat, &info);
}

/* ------------------------------------------------------- output (playback) */

static void output_apply_device(struct output *o, uint32_t rate)
{
	o->preamp_lin = o->dev.aeq_on ? powf(10.f, o->dev.preamp_db / 20.f) : 1.f;
	if (o->dev.aeq_on && o->dev.n > 0)
		ft_cascade_set(&o->aeq, o->dev.f, o->dev.n, rate ? rate : 48000, o->dev.opt_rate);
	else
		o->aeq.n = 0;
}

static void on_output_process(void *data)
{
	struct output *o = data;
	struct pw_buffer *b = pw_stream_dequeue_buffer(o->stream);
	if (!b)
		return;
	struct spa_data *d = &b->buffer->datas[0];
	float *dst = d->data;
	if (!dst) {
		pw_stream_queue_buffer(o->stream, b);
		return;
	}
	const uint32_t stride = sizeof(float) * 2;
	uint32_t n = d->maxsize / stride;
	if (b->requested && b->requested < n)
		n = b->requested;
	if (n > MAX_FRAMES)
		n = MAX_FRAMES;

	uint32_t avail = o->wr - o->rd;
	if (!o->primed) {
		/* Wait for one full quantum so capture/playback ordering inside a
		 * cycle never causes an underrun on the first buffers. */
		if (avail >= n)
			o->primed = true;
		avail = 0;
	}
	uint32_t take = avail < n ? avail : n;
	for (uint32_t i = 0; i < take; i++) {
		uint32_t idx = ((o->rd + i) % RING_FRAMES) * 2;
		dst[i * 2] = o->ring[idx];
		dst[i * 2 + 1] = o->ring[idx + 1];
	}
	o->rd += take;
	if (take < n)
		memset(dst + take * 2, 0, (n - take) * stride);

	d->chunk->offset = 0;
	d->chunk->stride = stride;
	d->chunk->size = n * stride;
	pw_stream_queue_buffer(o->stream, b);
}

static void on_output_state(void *data, enum pw_stream_state old, enum pw_stream_state state, const char *error)
{
	struct output *o = data;
	emit("state %s:%s %s %s", o->chain->id, o->target, pw_stream_state_as_string(state), error ? error : "");
}

static const struct pw_stream_events output_events = {
	PW_VERSION_STREAM_EVENTS,
	.process = on_output_process,
	.state_changed = on_output_state,
};

/* Write processed frames of the chain into one output's ring (data thread). */
static void output_feed(struct output *o, const float *src, float *tmp, uint32_t frames, uint32_t rate)
{
	memcpy(tmp, src, frames * 2 * sizeof(float));

	/* Equal-power fade for cross-fading device switches */
	if (o->fade_dir != 0) {
		/* an incoming output holds at silence until its stream is running */
		int dir = (o->fade_dir > 0 && !o->primed) ? 0 : o->fade_dir;
		for (uint32_t i = 0; i < frames; i++) {
			o->fade += dir * o->fade_step;
			if (o->fade >= 1.f) {
				o->fade = 1.f;
				o->fade_dir = 0;
			} else if (o->fade <= 0.f) {
				o->fade = 0.f;
				if (dir < 0)
					o->dead = true;
			}
			float g = sinf(o->fade * (float)M_PI_2);
			tmp[i * 2] *= g;
			tmp[i * 2 + 1] *= g;
		}
	}

	/* AutoEQ headphone correction for this device: preamp, then cascade */
	if (o->aeq.n > 0) {
		if (o->preamp_lin != 1.f)
			for (uint32_t i = 0; i < frames * 2; i++)
				tmp[i] *= o->preamp_lin;
		ft_cascade_process(&o->aeq, tmp, frames);
	}

	/* Software device volume (the "Software" volume tier) */
	float target = o->dev.mute ? 0.f : o->dev.gain;
	if (o->cur_dev_gain != target || target != 1.f) {
		float g = o->cur_dev_gain;
		for (uint32_t i = 0; i < frames; i++) {
			g += (target - g) * RAMP_COEF;
			tmp[i * 2] *= g;
			tmp[i * 2 + 1] *= g;
		}
		if (fabsf(g - target) < 1e-5f)
			g = target;
		o->cur_dev_gain = g;
	}

	ft_soft_limit_buffer(tmp, frames * 2);

	/* Latency guard: never let the ring grow beyond ~3 quanta. */
	uint32_t fill = o->wr - o->rd;
	if (fill > frames * 3 + 256)
		o->rd = o->wr - frames;
	if (o->wr - o->rd + frames > RING_FRAMES)
		o->rd = o->wr + frames - RING_FRAMES;
	for (uint32_t i = 0; i < frames; i++) {
		uint32_t idx = ((o->wr + i) % RING_FRAMES) * 2;
		o->ring[idx] = tmp[i * 2];
		o->ring[idx + 1] = tmp[i * 2 + 1];
	}
	o->wr += frames;
	(void)rate;
}

/* ------------------------------------------------------- chain (capture) */

static void on_capture_process(void *data)
{
	struct chain *c = data;
	struct pw_buffer *b = pw_stream_dequeue_buffer(c->capture);
	if (!b)
		return;
	struct spa_data *d = &b->buffer->datas[0];
	const uint32_t stride = sizeof(float) * 2;
	uint32_t offs = SPA_MIN(d->chunk->offset, d->maxsize);
	uint32_t size = SPA_MIN(d->chunk->size, d->maxsize - offs);
	uint32_t frames = size / stride;
	if (frames > MAX_FRAMES)
		frames = MAX_FRAMES;

	if (d->data && frames > 0) {
		const float *in = SPA_PTROFF(d->data, offs, const float);
		float *s = c->scratch;
		memcpy(s, in, frames * stride);

		/* Volume × boost with a de-click ramp; mute ramps to zero. */
		float target = c->muted ? 0.f : c->target_gain;
		float g = c->cur_gain;
		if (g != target || target != 1.f) {
			for (uint32_t i = 0; i < frames; i++) {
				g += (target - g) * RAMP_COEF;
				s[i * 2] *= g;
				s[i * 2 + 1] *= g;
			}
			if (fabsf(g - target) < 1e-5f)
				g = target;
			c->cur_gain = g;
		}

		if (c->eq_on)
			ft_cascade_process(&c->eq, s, frames);
		ft_leveler_process(&c->lev, s, frames);
		if (c->lc_on)
			ft_cascade_process(&c->lc, s, frames);

		float peak = 0.f;
		for (uint32_t i = 0; i < frames * 2; i++) {
			float a = fabsf(s[i]);
			if (a > peak)
				peak = a;
		}
		if (peak > 1.f)
			peak = 1.f;
		if (peak > c->peak)
			c->peak = peak;

		/* Warm-up: outputs being replaced keep playing until every incoming
		 * output has started consuming audio, then the equal-power fade runs. */
		bool incoming_ready = true;
		for (int i = 0; i < c->n_outs; i++)
			if (c->outs[i]->fade_dir > 0 && !c->outs[i]->primed)
				incoming_ready = false;
		for (int i = 0; i < c->n_outs; i++) {
			struct output *o = c->outs[i];
			if (o->fade_out_pending) {
				o->pending_frames += frames;
				if (incoming_ready || o->pending_frames > (uint32_t)(WARMUP_MAX_MS / 1000.0 * c->rate)) {
					o->fade_out_pending = false;
					o->fade_dir = -1;
				}
			}
		}
		for (int i = 0; i < c->n_outs; i++)
			if (!c->outs[i]->dead)
				output_feed(c->outs[i], s, c->oscratch, frames, c->rate);
	}
	pw_stream_queue_buffer(c->capture, b);
}

struct rate_msg { uint32_t rate; };

static int rt_set_rate(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	struct chain *c = user;
	const struct rate_msg *m = data;
	c->rate = m->rate;
	ft_cascade_set(&c->eq, c->eq_spec, FT_EQ_BANDS, c->rate, 0);
	ft_cascade_reset(&c->eq);
	ft_cascade_set(&c->lc, c->lc_spec, FT_LC_BANDS, c->rate, 0);
	ft_cascade_reset(&c->lc);
	ft_leveler_init(&c->lev, c->rate, c->lev.enabled);
	for (int i = 0; i < c->n_outs; i++) {
		output_apply_device(c->outs[i], c->rate);
		ft_cascade_reset(&c->outs[i]->aeq);
		c->outs[i]->fade_step = 1.f / (float)(FADE_MS / 1000.0 * c->rate);
	}
	return 0;
}

static void on_capture_param_changed(void *data, uint32_t id, const struct spa_pod *param)
{
	struct chain *c = data;
	if (param == NULL || id != SPA_PARAM_Format)
		return;
	struct spa_audio_info_raw info;
	memset(&info, 0, sizeof(info));
	if (spa_format_audio_raw_parse(param, &info) < 0)
		return;
	if (info.rate == 0 || info.rate == c->rate)
		return;
	struct rate_msg m = { info.rate };
	run_rt(rt_set_rate, &m, sizeof(m), c);
	emit("rate %s %u", c->id, info.rate);
}

static void on_capture_state(void *data, enum pw_stream_state old, enum pw_stream_state state, const char *error)
{
	struct chain *c = data;
	emit("state %s %s %s", c->id, pw_stream_state_as_string(state), error ? error : "");
}

static const struct pw_stream_events capture_events = {
	PW_VERSION_STREAM_EVENTS,
	.process = on_capture_process,
	.param_changed = on_capture_param_changed,
	.state_changed = on_capture_state,
};

static void chain_create(const char *id, const char *node_name, const char *desc)
{
	if (find_chain(id)) {
		emit("err chain %s exists", id);
		return;
	}
	struct chain *c = calloc(1, sizeof(*c));
	if (!c) {
		emit("err oom");
		return;
	}
	c->engine = &E;
	snprintf(c->id, sizeof(c->id), "%s", id);
	snprintf(c->node_name, sizeof(c->node_name), "%s", node_name);
	snprintf(c->desc, sizeof(c->desc), "%s", desc);
	spa_list_init(&c->outputs);
	c->rate = 48000;
	c->target_gain = 1.f;
	c->cur_gain = 1.f;
	c->eq_on = false;
	c->active = true;
	for (int i = 0; i < FT_EQ_BANDS; i++) {
		c->eq_spec[i].type = FT_PEAKING;
		c->eq_spec[i].freq = EQ_FREQS[i];
		c->eq_spec[i].gain_db = 0;
		c->eq_spec[i].q = EQ_Q;
	}
	for (int i = 0; i < FT_LC_BANDS; i++)
		c->lc_spec[i] = LC_TOPOLOGY[i];
	ft_cascade_set(&c->eq, c->eq_spec, FT_EQ_BANDS, c->rate, 0);
	ft_cascade_set(&c->lc, c->lc_spec, FT_LC_BANDS, c->rate, 0);
	ft_leveler_init(&c->lev, c->rate, false);

	char group[200];
	snprintf(group, sizeof(group), "finetune.group.%s", id);
	struct pw_properties *props = pw_properties_new(
		PW_KEY_MEDIA_CLASS, "Audio/Sink",
		PW_KEY_NODE_NAME, node_name,
		PW_KEY_NODE_DESCRIPTION, desc,
		PW_KEY_NODE_VIRTUAL, "true",
		PW_KEY_NODE_GROUP, group,
		PW_KEY_NODE_LINK_GROUP, group,
		PW_KEY_APP_NAME, "FineTune",
		PW_KEY_DEVICE_ICON_NAME, "audio-card",
		SPA_KEY_AUDIO_POSITION, "FL,FR",
		"priority.session", "0",
		"priority.driver", "0",
		"finetune.engine", "true",
		"finetune.chain", id,
		NULL);
	c->capture = pw_stream_new(E.core, node_name, props);
	if (!c->capture) {
		emit("err cannot create stream for %s", id);
		free(c);
		return;
	}
	pw_stream_add_listener(c->capture, &c->listener, &capture_events, c);

	uint8_t buffer[1024];
	struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
	const struct spa_pod *params[1] = { build_format(&b) };
	int res = pw_stream_connect(c->capture, PW_DIRECTION_INPUT, PW_ID_ANY,
				    PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS | PW_STREAM_FLAG_RT_PROCESS,
				    params, 1);
	if (res < 0) {
		emit("err connect %s: %s", id, spa_strerror(res));
		pw_stream_destroy(c->capture);
		free(c);
		return;
	}
	spa_list_append(&E.chains, &c->link);
}

struct ptr_msg { void *ptr; };

static int rt_attach_output(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	struct chain *c = user;
	struct output *o = ((const struct ptr_msg *)data)->ptr;
	if (c->n_outs >= MAX_OUTPUTS)
		return -ENOSPC;
	o->fade_step = 1.f / (float)(FADE_MS / 1000.0 * c->rate);
	output_apply_device(o, c->rate);
	c->outs[c->n_outs++] = o;
	o->in_chain = true;
	return 0;
}

static int rt_fade_out(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	struct output *o = ((const struct ptr_msg *)data)->ptr;
	o->fade_out_pending = true;
	o->pending_frames = 0;
	if (o->fade <= 0.f)
		o->dead = true;
	return 0;
}

static int rt_fade_in(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	struct output *o = ((const struct ptr_msg *)data)->ptr;
	o->dead = false;
	o->fade_out_pending = false;
	o->fade_dir = o->fade >= 1.f ? 0 : 1;
	return 0;
}

static int rt_detach_output(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	struct chain *c = user;
	struct output *o = ((const struct ptr_msg *)data)->ptr;
	for (int i = 0; i < c->n_outs; i++) {
		if (c->outs[i] == o) {
			memmove(&c->outs[i], &c->outs[i + 1], sizeof(c->outs[0]) * (size_t)(c->n_outs - i - 1));
			c->n_outs--;
			break;
		}
	}
	o->in_chain = false;
	return 0;
}

static void output_destroy(struct output *o)
{
	struct chain *c = o->chain;
	if (o->in_chain) {
		struct ptr_msg m = { o };
		run_rt(rt_detach_output, &m, sizeof(m), c);
	}
	spa_list_remove(&o->link);
	if (o->stream)
		pw_stream_destroy(o->stream);
	free(o);
}

static struct output *output_create(struct chain *c, const char *target, bool fade_in)
{
	struct output *o = calloc(1, sizeof(*o));
	if (!o) {
		emit("err oom");
		return NULL;
	}
	o->chain = c;
	snprintf(o->target, sizeof(o->target), "%s", target);
	struct device *dev = find_device(target);
	o->dev = dev ? dev->p : default_device_params();
	o->cur_dev_gain = o->dev.mute ? 0.f : o->dev.gain;
	o->fade = fade_in ? 0.f : 1.f;
	o->fade_dir = fade_in ? 1 : 0;

	char group[200], name[256], desc[512];
	snprintf(group, sizeof(group), "finetune.group.%s", c->id);
	snprintf(name, sizeof(name), "finetune_out.%s.%d", c->id, ++E.out_seq);
	snprintf(desc, sizeof(desc), "%s → output", c->desc);
	struct pw_properties *props = pw_properties_new(
		PW_KEY_MEDIA_TYPE, "Audio",
		PW_KEY_MEDIA_CATEGORY, "Playback",
		PW_KEY_NODE_NAME, name,
		PW_KEY_NODE_DESCRIPTION, desc,
		PW_KEY_NODE_GROUP, group,
		PW_KEY_NODE_LINK_GROUP, group,
		PW_KEY_TARGET_OBJECT, target,
		PW_KEY_APP_NAME, "FineTune",
		SPA_KEY_AUDIO_POSITION, "FL,FR",
		"finetune.engine", "true",
		"finetune.chain", c->id,
		"node.dont-reconnect", "true",
		"stream.dont-remix", "false",
		NULL);
	o->stream = pw_stream_new(E.core, name, props);
	if (!o->stream) {
		emit("err cannot create output %s", target);
		free(o);
		return NULL;
	}
	pw_stream_add_listener(o->stream, &o->listener, &output_events, o);
	uint8_t buffer[1024];
	struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
	const struct spa_pod *params[1] = { build_format(&b) };
	int res = pw_stream_connect(o->stream, PW_DIRECTION_OUTPUT, PW_ID_ANY,
				    PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS | PW_STREAM_FLAG_RT_PROCESS,
				    params, 1);
	if (res < 0) {
		emit("err connect output %s: %s", target, spa_strerror(res));
		pw_stream_destroy(o->stream);
		free(o);
		return NULL;
	}
	if (!c->active)
		pw_stream_set_active(o->stream, false);
	spa_list_append(&c->outputs, &o->link);
	struct ptr_msg m = { o };
	run_rt(rt_attach_output, &m, sizeof(m), c);
	return o;
}

static void chain_set_outputs(struct chain *c, char **targets, int n)
{
	bool had_outputs = !spa_list_is_empty(&c->outputs);
	struct output *o, *t;
	/* Outputs no longer wanted fade out (cross-fade); kept ones fade back in. */
	spa_list_for_each_safe(o, t, &c->outputs, link) {
		bool keep = false;
		for (int i = 0; i < n; i++)
			if (strcmp(o->target, targets[i]) == 0)
				keep = true;
		struct ptr_msg m = { o };
		run_rt(keep ? rt_fade_in : rt_fade_out, &m, sizeof(m), c);
	}
	for (int i = 0; i < n && i < MAX_OUTPUTS; i++) {
		bool exists = false;
		spa_list_for_each(o, &c->outputs, link)
			if (strcmp(o->target, targets[i]) == 0)
				exists = true;
		if (!exists)
			output_create(c, targets[i], true);
	}
	(void)had_outputs;
}

static void chain_destroy(struct chain *c)
{
	struct output *o, *t;
	spa_list_for_each_safe(o, t, &c->outputs, link)
		output_destroy(o);
	spa_list_remove(&c->link);
	pw_stream_destroy(c->capture);
	free(c);
}

/* ------------------------------------------------------- parameter updates */

struct gain_msg { float gain; int mute; int which; };

static int rt_gain(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	struct chain *c = user;
	const struct gain_msg *m = data;
	if (m->which == 0)
		c->target_gain = m->gain;
	else
		c->muted = m->mute != 0;
	return 0;
}

struct eq_msg { int on; float g[FT_EQ_BANDS]; int kind; int lev; };

static int rt_eq(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	struct chain *c = user;
	const struct eq_msg *m = data;
	if (m->kind == 0) {
		for (int i = 0; i < FT_EQ_BANDS; i++)
			c->eq_spec[i].gain_db = m->g[i];
		ft_cascade_set(&c->eq, c->eq_spec, FT_EQ_BANDS, c->rate, 0);
		c->eq_on = m->on != 0;
	} else if (m->kind == 1) {
		for (int i = 0; i < FT_LC_BANDS; i++)
			c->lc_spec[i].gain_db = m->g[i];
		ft_cascade_set(&c->lc, c->lc_spec, FT_LC_BANDS, c->rate, 0);
		c->lc_on = m->on != 0;
	} else {
		bool on = m->lev != 0;
		if (on && !c->lev.enabled)
			ft_leveler_init(&c->lev, c->rate, true);
		c->lev.enabled = on;
	}
	return 0;
}

struct dev_msg { char target[256]; struct device_params p; };

static int rt_device(struct spa_loop *l, bool async, uint32_t seq, const void *data, size_t size, void *user)
{
	const struct dev_msg *m = data;
	struct chain *c;
	spa_list_for_each(c, &E.chains, link) {
		for (int i = 0; i < c->n_outs; i++) {
			struct output *o = c->outs[i];
			if (strcmp(o->target, m->target) != 0)
				continue;
			bool aeq_changed = o->dev.aeq_on != m->p.aeq_on || o->dev.n != m->p.n;
			o->dev = m->p;
			output_apply_device(o, c->rate);
			if (aeq_changed)
				ft_cascade_reset(&o->aeq);
		}
	}
	return 0;
}

/* ------------------------------------------------------------- meters */

static void on_meter_process(void *data)
{
	struct meter *m = data;
	struct pw_buffer *b = pw_stream_dequeue_buffer(m->stream);
	if (!b)
		return;
	struct spa_data *d = &b->buffer->datas[0];
	uint32_t offs = SPA_MIN(d->chunk->offset, d->maxsize);
	uint32_t size = SPA_MIN(d->chunk->size, d->maxsize - offs);
	if (d->data) {
		const float *p = SPA_PTROFF(d->data, offs, const float);
		float peak = 0.f;
		for (uint32_t i = 0; i < size / sizeof(float); i++) {
			float a = fabsf(p[i]);
			if (a > peak)
				peak = a;
		}
		if (peak > 1.f)
			peak = 1.f;
		if (peak > m->peak)
			m->peak = peak;
	}
	pw_stream_queue_buffer(m->stream, b);
}

static const struct pw_stream_events meter_events = {
	PW_VERSION_STREAM_EVENTS,
	.process = on_meter_process,
};

static void meter_destroy(struct meter *m)
{
	spa_list_remove(&m->link);
	pw_stream_destroy(m->stream);
	free(m);
}

static void meter_create(const char *id, const char *source)
{
	struct meter *m;
	spa_list_for_each(m, &E.meters, link)
		if (strcmp(m->id, id) == 0) {
			meter_destroy(m);
			break;
		}
	m = calloc(1, sizeof(*m));
	if (!m)
		return;
	snprintf(m->id, sizeof(m->id), "%s", id);
	char name[200];
	snprintf(name, sizeof(name), "finetune_meter.%s", id);
	struct pw_properties *props = pw_properties_new(
		PW_KEY_MEDIA_TYPE, "Audio",
		PW_KEY_MEDIA_CATEGORY, "Monitor",
		PW_KEY_NODE_NAME, name,
		PW_KEY_NODE_DESCRIPTION, "FineTune level meter",
		PW_KEY_TARGET_OBJECT, source,
		PW_KEY_APP_NAME, "FineTune",
		PW_KEY_NODE_LATENCY, "1024/48000",
		"node.passive", "true",
		"node.dont-reconnect", "true",
		"finetune.engine", "true",
		NULL);
	m->stream = pw_stream_new(E.core, name, props);
	if (!m->stream) {
		free(m);
		return;
	}
	pw_stream_add_listener(m->stream, &m->listener, &meter_events, m);
	uint8_t buffer[1024];
	struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
	const struct spa_pod *params[1] = { build_format(&b) };
	if (pw_stream_connect(m->stream, PW_DIRECTION_INPUT, PW_ID_ANY,
			      PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS | PW_STREAM_FLAG_RT_PROCESS,
			      params, 1) < 0) {
		pw_stream_destroy(m->stream);
		free(m);
		return;
	}
	spa_list_append(&E.meters, &m->link);
}

/* -------------------------------------------------------------- commands */

static int split(char *line, char **argv, int max)
{
	int n = 0;
	char *save = NULL;
	for (char *tok = strtok_r(line, " \t", &save); tok && n < max; tok = strtok_r(NULL, " \t", &save))
		argv[n++] = tok;
	return n;
}

static int filter_type(const char *s)
{
	switch (s[0]) {
	case 'L': case 'l': return FT_LOWSHELF;
	case 'H': case 'h': return FT_HIGHSHELF;
	default: return FT_PEAKING;
	}
}

static void handle_line(char *line)
{
	char raw[4096];
	snprintf(raw, sizeof(raw), "%s", line);
	char *argv[64];
	int argc = split(line, argv, 64);
	if (argc == 0)
		return;
	const char *cmd = argv[0];

	if (strcmp(cmd, "ping") == 0) {
		emit("pong");
	} else if (strcmp(cmd, "quit") == 0) {
		pw_main_loop_quit(E.loop);
	} else if (strcmp(cmd, "chain") == 0 && argc >= 3) {
		/* description is the remainder of the raw line */
		const char *desc = argv[2];
		char *p = raw;
		for (int skip = 0; skip < 3 && p; skip++) {
			p = strpbrk(p, " \t");
			if (p)
				p += strspn(p, " \t");
		}
		if (p && *p)
			desc = p;
		chain_create(argv[1], argv[2], desc);
	} else if (argc >= 2 && strcmp(cmd, "meter") == 0 && argc >= 3) {
		meter_create(argv[1], argv[2]);
	} else if (argc >= 2 && strcmp(cmd, "unmeter") == 0) {
		struct meter *m;
		spa_list_for_each(m, &E.meters, link)
			if (strcmp(m->id, argv[1]) == 0) {
				meter_destroy(m);
				break;
			}
	} else if (strcmp(cmd, "dev") == 0 && argc >= 8) {
		struct dev_msg m;
		memset(&m, 0, sizeof(m));
		snprintf(m.target, sizeof(m.target), "%s", argv[1]);
		m.p.gain = strtof(argv[2], NULL);
		if (!isfinite(m.p.gain) || m.p.gain < 0)
			m.p.gain = 1.f;
		m.p.mute = atoi(argv[3]) != 0;
		m.p.aeq_on = atoi(argv[4]) != 0;
		m.p.preamp_db = ft_clampf(strtof(argv[5], NULL), -30.f, 30.f);
		m.p.opt_rate = strtod(argv[6], NULL);
		int n = atoi(argv[7]);
		if (n < 0)
			n = 0;
		if (n > FT_MAX_SECTIONS)
			n = FT_MAX_SECTIONS;
		int k = 0;
		for (int i = 0; i < n && 8 + i * 4 + 3 < argc; i++) {
			struct ft_filter_spec s = {
				filter_type(argv[8 + i * 4]),
				strtod(argv[9 + i * 4], NULL),
				strtod(argv[10 + i * 4], NULL),
				strtod(argv[11 + i * 4], NULL),
			};
			if (s.freq > 0 && s.q > 0 && fabs(s.gain_db) <= 30)
				m.p.f[k++] = s;
		}
		m.p.n = k;
		struct device *d = find_device(m.target);
		if (!d) {
			d = calloc(1, sizeof(*d));
			if (!d)
				return;
			snprintf(d->target, sizeof(d->target), "%s", m.target);
			spa_list_append(&E.devices, &d->link);
		}
		d->p = m.p;
		run_rt(rt_device, &m, sizeof(m), NULL);
	} else {
		if (argc < 2) {
			emit("err bad command %s", cmd);
			return;
		}
		struct chain *c = find_chain(argv[1]);
		if (!c) {
			emit("err no chain %s", argv[1]);
			return;
		}
		if (strcmp(cmd, "drop") == 0) {
			chain_destroy(c);
		} else if (strcmp(cmd, "outs") == 0) {
			chain_set_outputs(c, argv + 2, argc - 2);
		} else if (strcmp(cmd, "gain") == 0 && argc >= 3) {
			struct gain_msg m = { strtof(argv[2], NULL), 0, 0 };
			if (!isfinite(m.gain) || m.gain < 0)
				m.gain = 0;
			if (m.gain > 16.f)
				m.gain = 16.f;
			run_rt(rt_gain, &m, sizeof(m), c);
		} else if (strcmp(cmd, "mute") == 0 && argc >= 3) {
			struct gain_msg m = { 0, atoi(argv[2]), 1 };
			run_rt(rt_gain, &m, sizeof(m), c);
		} else if (strcmp(cmd, "active") == 0 && argc >= 3) {
			bool on = atoi(argv[2]) != 0;
			if (on != c->active) {
				c->active = on;
				pw_stream_set_active(c->capture, on);
				struct output *o;
				spa_list_for_each(o, &c->outputs, link)
					pw_stream_set_active(o->stream, on);
			}
		} else if (strcmp(cmd, "eq") == 0 && argc >= 3 + FT_EQ_BANDS) {
			struct eq_msg m;
			memset(&m, 0, sizeof(m));
			m.kind = 0;
			m.on = atoi(argv[2]);
			for (int i = 0; i < FT_EQ_BANDS; i++)
				m.g[i] = ft_clampf(strtof(argv[3 + i], NULL), -12.f, 12.f);
			run_rt(rt_eq, &m, sizeof(m), c);
		} else if (strcmp(cmd, "lc") == 0 && argc >= 3 + FT_LC_BANDS) {
			struct eq_msg m;
			memset(&m, 0, sizeof(m));
			m.kind = 1;
			m.on = atoi(argv[2]);
			for (int i = 0; i < FT_LC_BANDS; i++)
				m.g[i] = ft_clampf(strtof(argv[3 + i], NULL), -30.f, 30.f);
			run_rt(rt_eq, &m, sizeof(m), c);
		} else if (strcmp(cmd, "lev") == 0 && argc >= 3) {
			struct eq_msg m;
			memset(&m, 0, sizeof(m));
			m.kind = 2;
			m.lev = atoi(argv[2]);
			run_rt(rt_eq, &m, sizeof(m), c);
		} else {
			emit("err bad command %s", cmd);
		}
	}
}

static void on_stdin(void *data, int fd, uint32_t mask)
{
	if (mask & (SPA_IO_IN)) {
		ssize_t r = read(fd, E.inbuf + E.inlen, sizeof(E.inbuf) - E.inlen - 1);
		if (r > 0) {
			E.inlen += (size_t)r;
			E.inbuf[E.inlen] = 0;
			char *start = E.inbuf, *nl;
			while ((nl = memchr(start, '\n', E.inlen - (size_t)(start - E.inbuf)))) {
				*nl = 0;
				handle_line(start);
				start = nl + 1;
			}
			size_t rest = E.inlen - (size_t)(start - E.inbuf);
			memmove(E.inbuf, start, rest);
			E.inlen = rest;
			if (E.inlen >= sizeof(E.inbuf) - 1)
				E.inlen = 0; /* overlong garbage line */
			return;
		}
		if (r < 0 && (errno == EAGAIN || errno == EINTR))
			return;
		pw_main_loop_quit(E.loop);
		return;
	}
	if (mask & (SPA_IO_HUP | SPA_IO_ERR))
		pw_main_loop_quit(E.loop);
}

/* Periodic housekeeping: report peak levels and reap faded-out outputs. */
static void on_timer(void *data, uint64_t expirations)
{
	char line[8192];
	size_t len = 0;
	bool any = false;
	len += (size_t)snprintf(line + len, sizeof(line) - len, "lv");
	struct chain *c;
	spa_list_for_each(c, &E.chains, link) {
		float p = c->peak;
		c->peak = 0;
		if (len < sizeof(line) - 128)
			len += (size_t)snprintf(line + len, sizeof(line) - len, " %s=%.4f", c->id, p);
		any = true;

		struct output *o, *t;
		spa_list_for_each_safe(o, t, &c->outputs, link)
			if (o->dead)
				output_destroy(o);
	}
	struct meter *m;
	spa_list_for_each(m, &E.meters, link) {
		float p = m->peak;
		m->peak = 0;
		if (len < sizeof(line) - 128)
			len += (size_t)snprintf(line + len, sizeof(line) - len, " %s=%.4f", m->id, p);
		any = true;
	}
	if (any)
		emit("%s", line);
}

static void on_core_error(void *data, uint32_t id, int seq, int res, const char *message)
{
	emit("err core id=%u %s", id, message ? message : "");
	if (id == PW_ID_CORE && res == -EPIPE)
		pw_main_loop_quit(E.loop);
}

static const struct pw_core_events core_events = {
	PW_VERSION_CORE_EVENTS,
	.error = on_core_error,
};

static void do_quit(void *data, int signal_number)
{
	pw_main_loop_quit(E.loop);
}

int main(int argc, char *argv[])
{
	pw_init(&argc, &argv);
	memset(&E, 0, sizeof(E));
	spa_list_init(&E.chains);
	spa_list_init(&E.devices);
	spa_list_init(&E.meters);

	E.loop = pw_main_loop_new(NULL);
	if (!E.loop) {
		fprintf(stderr, "cannot create main loop\n");
		return 1;
	}
	struct pw_loop *l = pw_main_loop_get_loop(E.loop);
	pw_loop_add_signal(l, SIGINT, do_quit, NULL);
	pw_loop_add_signal(l, SIGTERM, do_quit, NULL);

	E.context = pw_context_new(l,
		pw_properties_new("context.num-data-loops", "1", NULL), 0);
	if (!E.context) {
		fprintf(stderr, "cannot create context\n");
		return 1;
	}
	E.data_loop = pw_context_get_data_loop(E.context);
	E.core = pw_context_connect(E.context,
		pw_properties_new(PW_KEY_APP_NAME, "FineTune Engine", NULL), 0);
	if (!E.core) {
		emit("err cannot connect to PipeWire: %s", strerror(errno));
		return 1;
	}
	pw_core_add_listener(E.core, &E.core_listener, &core_events, NULL);

	fcntl(STDIN_FILENO, F_SETFL, fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK);
	pw_loop_add_io(l, STDIN_FILENO, SPA_IO_IN | SPA_IO_HUP | SPA_IO_ERR, false, on_stdin, NULL);

	E.timer = pw_loop_add_timer(l, on_timer, NULL);
	struct timespec value = { 0, 50 * 1000000 }, interval = { 0, 50 * 1000000 };
	pw_loop_update_timer(l, E.timer, &value, &interval, false);

	emit("ready");
	pw_main_loop_run(E.loop);

	struct chain *c, *ct;
	spa_list_for_each_safe(c, ct, &E.chains, link)
		chain_destroy(c);
	struct meter *m, *mt;
	spa_list_for_each_safe(m, mt, &E.meters, link)
		meter_destroy(m);
	struct device *d, *dt;
	spa_list_for_each_safe(d, dt, &E.devices, link) {
		spa_list_remove(&d->link);
		free(d);
	}
	pw_core_disconnect(E.core);
	pw_context_destroy(E.context);
	pw_main_loop_destroy(E.loop);
	pw_deinit();
	return 0;
}
