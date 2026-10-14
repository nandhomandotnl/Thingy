/*
 * Gyroscope + accelerometer -> relative mouse movement ("air mouse").
 * Plain C without Zephyr dependencies so it can be unit tested on a PC.
 */
#ifndef AIRMOUSE_H_
#define AIRMOUSE_H_

#include <stdbool.h>

struct airmouse_cfg {
	float sample_rate_hz;
	/* Mouse counts per degree of rotation at slow speed. */
	float sensitivity;
	/* Extra gain per 100 deg/s (pointer acceleration). */
	float acceleration;
	/* Rates below this (deg/s) are ignored to kill hand jitter. */
	float deadzone_dps;
	/* Exponential smoothing, 0 = none, closer to 1 = smoother but laggier. */
	float smoothing;
	/* Which sensor axis points "forward" (away from you):
	 * 0 = +Y, 1 = -X, 2 = -Y, 3 = +X (each step turns 90 degrees).
	 */
	int forward;
	bool invert_x;
	bool invert_y;
	/* Low-pass factor for the gravity estimate per sample. */
	float gravity_alpha;
	/* Bias calibration. */
	int calibration_samples;
	float still_threshold_dps;
	int still_samples;
	float bias_adapt_rate;
};

struct airmouse {
	struct airmouse_cfg cfg;
	float bias[3];
	float up[3];
	bool have_up;
	float cal_sum[3];
	int cal_count;
	int still_count;
	float smooth[2];
	float remainder[2];
};

void airmouse_init(struct airmouse *am, const struct airmouse_cfg *cfg);
void airmouse_recalibrate(struct airmouse *am);
void airmouse_reset_motion(struct airmouse *am);
bool airmouse_calibrated(const struct airmouse *am);

/*
 * Feed one sample: gyro in deg/s and accelerometer in any unit (only its direction is used).
 * Turning around the vertical axis (yaw) moves the cursor horizontally and tilting the
 * front up/down (pitch) moves it vertically, however the device is rolled in the hand.
 */
void airmouse_update(struct airmouse *am, const float gyro_dps[3], const float accel[3],
		     int *dx, int *dy);

#endif /* AIRMOUSE_H_ */
