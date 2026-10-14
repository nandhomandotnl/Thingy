#include "airmouse.h"

#include <math.h>
#include <string.h>

void airmouse_init(struct airmouse *am, const struct airmouse_cfg *cfg)
{
	memset(am, 0, sizeof(*am));
	am->cfg = *cfg;
}

void airmouse_reset_motion(struct airmouse *am)
{
	am->smooth[0] = am->smooth[1] = 0.0f;
	am->remainder[0] = am->remainder[1] = 0.0f;
}

void airmouse_recalibrate(struct airmouse *am)
{
	memset(am->cal_sum, 0, sizeof(am->cal_sum));
	am->cal_count = 0;
	am->still_count = 0;
	airmouse_reset_motion(am);
}

bool airmouse_calibrated(const struct airmouse *am)
{
	return am->cal_count >= am->cfg.calibration_samples;
}

static void calibrate(struct airmouse *am, const float g[3])
{
	/* Start over if the device is moved during calibration. */
	if (am->cal_count > 0) {
		for (int i = 0; i < 3; i++) {
			float mean = am->cal_sum[i] / am->cal_count;

			if (fabsf(g[i] - mean) > am->cfg.still_threshold_dps) {
				airmouse_recalibrate(am);
				break;
			}
		}
	}

	for (int i = 0; i < 3; i++) {
		am->cal_sum[i] += g[i];
	}
	am->cal_count++;

	if (airmouse_calibrated(am)) {
		for (int i = 0; i < 3; i++) {
			am->bias[i] = am->cal_sum[i] / am->cal_count;
		}
	}
}

static void track_bias(struct airmouse *am, const float g[3], const float rates[3])
{
	bool still = true;

	for (int i = 0; i < 3; i++) {
		if (fabsf(rates[i]) >= am->cfg.still_threshold_dps) {
			still = false;
		}
	}
	am->still_count = still ? am->still_count + 1 : 0;

	if (am->still_count >= am->cfg.still_samples) {
		float k = am->cfg.bias_adapt_rate;

		for (int i = 0; i < 3; i++) {
			am->bias[i] = (1.0f - k) * am->bias[i] + k * g[i];
		}
	}
}

static float rate_to_counts(const struct airmouse_cfg *cfg, float rate, float dt)
{
	float magnitude = fabsf(rate) - cfg->deadzone_dps;

	if (magnitude <= 0.0f) {
		return 0.0f;
	}

	float gain = cfg->sensitivity * (1.0f + cfg->acceleration * magnitude / 100.0f);

	return copysignf(magnitude * dt * gain, rate);
}

static float dot(const float a[3], const float b[3])
{
	return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

static void cross(const float a[3], const float b[3], float out[3])
{
	out[0] = a[1] * b[2] - a[2] * b[1];
	out[1] = a[2] * b[0] - a[0] * b[2];
	out[2] = a[0] * b[1] - a[1] * b[0];
}

static bool normalize(float v[3])
{
	float n = sqrtf(dot(v, v));

	if (n < 1e-6f) {
		return false;
	}
	for (int i = 0; i < 3; i++) {
		v[i] /= n;
	}
	return true;
}

static void forward_axis(int step, float out[3])
{
	static const float axes[4][3] = {{0, 1, 0}, {-1, 0, 0}, {0, -1, 0}, {1, 0, 0}};

	for (int i = 0; i < 3; i++) {
		out[i] = axes[((step % 4) + 4) % 4][i];
	}
}

/* Yaw rate around "up" and pitch rate around the horizontal "right" axis. */
static void yaw_pitch(const struct airmouse *am, const float rates[3], float *yaw, float *pitch)
{
	float up[3] = {am->up[0], am->up[1], am->up[2]};
	float fwd[3], right[3];

	if (!normalize(up)) {
		*yaw = rates[2];
		*pitch = rates[0];
		return;
	}

	/* Project the chosen forward axis onto the horizontal plane. If it points (nearly)
	 * straight up or down, the next axis is used instead.
	 */
	for (int attempt = 0; attempt < 2; attempt++) {
		forward_axis(am->cfg.forward + attempt, fwd);
		float d = dot(fwd, up);

		for (int i = 0; i < 3; i++) {
			fwd[i] -= d * up[i];
		}
		if (dot(fwd, fwd) > 0.1f) {
			break;
		}
	}
	normalize(fwd);
	cross(fwd, up, right);

	*yaw = dot(rates, up);
	*pitch = dot(rates, right);
}

void airmouse_update(struct airmouse *am, const float gyro_dps[3], const float accel[3],
		     int *dx, int *dy)
{
	const struct airmouse_cfg *cfg = &am->cfg;
	float rates[3];
	float yaw, pitch;
	int out[2];

	*dx = 0;
	*dy = 0;

	/* A resting accelerometer measures +1 g pointing up. */
	if (!am->have_up) {
		for (int i = 0; i < 3; i++) {
			am->up[i] = accel[i];
		}
		am->have_up = true;
	} else {
		for (int i = 0; i < 3; i++) {
			am->up[i] += cfg->gravity_alpha * (accel[i] - am->up[i]);
		}
	}

	if (!airmouse_calibrated(am)) {
		calibrate(am, gyro_dps);
		return;
	}

	for (int i = 0; i < 3; i++) {
		rates[i] = gyro_dps[i] - am->bias[i];
	}
	track_bias(am, gyro_dps, rates);
	yaw_pitch(am, rates, &yaw, &pitch);

	/* Turning left (positive yaw) moves left and tilting up (positive pitch) moves up,
	 * while screen coordinates grow to the right and down; hence the default inversion.
	 */
	const float axis_rates[2] = {yaw, pitch};
	const bool invert[2] = {cfg->invert_x, cfg->invert_y};
	float dt = 1.0f / cfg->sample_rate_hz;

	for (int i = 0; i < 2; i++) {
		float rate = invert[i] ? -axis_rates[i] : axis_rates[i];

		am->smooth[i] = cfg->smoothing * am->smooth[i] + (1.0f - cfg->smoothing) * rate;

		float counts = rate_to_counts(cfg, am->smooth[i], dt) + am->remainder[i];
		float whole = truncf(counts);

		am->remainder[i] = counts - whole;
		out[i] = (int)whole;
	}

	*dx = out[0];
	*dy = out[1];
}
