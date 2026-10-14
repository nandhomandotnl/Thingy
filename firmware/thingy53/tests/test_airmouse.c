/* Host unit tests for the air mouse algorithm: cc tests/test_airmouse.c src/airmouse.c -lm */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "../src/airmouse.h"

static const struct airmouse_cfg base = {
	.sample_rate_hz = 100.0f,
	.sensitivity = 10.0f,
	.acceleration = 0.0f,
	.deadzone_dps = 0.0f,
	.smoothing = 0.0f,
	.forward = 0,
	.invert_x = true,
	.invert_y = true,
	.gravity_alpha = 0.02f,
	.calibration_samples = 10,
	.still_threshold_dps = 4.0f,
	.still_samples = 50,
	.bias_adapt_rate = 0.02f,
};

static void calibrate(struct airmouse *am, const float accel[3])
{
	const float zero[3] = {0};
	int dx, dy;

	for (int i = 0; i < 10; i++) {
		airmouse_update(am, zero, accel, &dx, &dy);
	}
	assert(airmouse_calibrated(am));
}

static void run(struct airmouse *am, const float gyro[3], const float accel[3], int n,
		int *sx, int *sy)
{
	int dx, dy;

	*sx = *sy = 0;
	for (int i = 0; i < n; i++) {
		airmouse_update(am, gyro, accel, &dx, &dy);
		*sx += dx;
		*sy += dy;
	}
}

static void test_flat(void)
{
	struct airmouse am;
	const float flat[3] = {0, 0, 9.81f};
	int sx, sy;

	airmouse_init(&am, &base);
	calibrate(&am, flat);

	/* Turn left 30 deg/s for 1 s around the vertical (+Z) axis -> 300 counts left. */
	run(&am, (float[3]){0, 0, 30}, flat, 100, &sx, &sy);
	assert(sx >= -301 && sx <= -299 && sy == 0);

	/* Tilt the front (+Y) up: rotation around +X (right) -> cursor up. */
	run(&am, (float[3]){20, 0, 0}, flat, 100, &sx, &sy);
	assert(sx == 0 && sy <= -199 && sy >= -201);
}

static void test_rolled_on_side(void)
{
	struct airmouse am;
	/* Device rolled 90 degrees so its +X axis points up. */
	const float side[3] = {9.81f, 0, 0};
	int sx, sy;

	airmouse_init(&am, &base);
	calibrate(&am, side);

	/* Turning left is now rotation around the device's X axis; must still go left. */
	run(&am, (float[3]){30, 0, 0}, side, 100, &sx, &sy);
	assert(sx <= -299 && sy == 0);
}

static void test_bias_and_deadzone(void)
{
	struct airmouse_cfg cfg = base;
	struct airmouse am;
	const float flat[3] = {0, 0, 9.81f};
	const float biased[3] = {1.0f, -2.0f, 0.5f};
	int dx, dy, sx, sy;

	cfg.deadzone_dps = 3.0f;
	airmouse_init(&am, &cfg);
	for (int i = 0; i < 10; i++) {
		airmouse_update(&am, biased, flat, &dx, &dy);
	}
	assert(airmouse_calibrated(&am));
	run(&am, biased, flat, 500, &sx, &sy);
	assert(sx == 0 && sy == 0);
	run(&am, (float[3]){1.0f, -2.0f, 2.5f}, flat, 100, &sx, &sy); /* 2 deg/s: in deadzone */
	assert(sx == 0 && sy == 0);
}

static void test_moving_during_calibration_restarts(void)
{
	struct airmouse am;
	const float flat[3] = {0, 0, 9.81f};
	int dx, dy;

	airmouse_init(&am, &base);
	for (int i = 0; i < 9; i++) {
		airmouse_update(&am, (float[3]){0, 0, 0}, flat, &dx, &dy);
	}
	airmouse_update(&am, (float[3]){0, 0, 50}, flat, &dx, &dy);
	assert(!airmouse_calibrated(&am));
}

static void test_forward_axis_rotation(void)
{
	struct airmouse_cfg cfg = base;
	struct airmouse am;
	const float flat[3] = {0, 0, 9.81f};
	int sx, sy;

	/* forward = -X (step 1): right axis is +Y, tilting front up is rotation around +Y. */
	cfg.forward = 1;
	airmouse_init(&am, &cfg);
	calibrate(&am, flat);
	run(&am, (float[3]){0, 20, 0}, flat, 100, &sx, &sy);
	assert(sx == 0 && sy <= -199);
}

int main(void)
{
	test_flat();
	test_rolled_on_side();
	test_bias_and_deadzone();
	test_moving_during_calibration_restarts();
	test_forward_axis_rotation();
	printf("all airmouse tests passed\n");
	return 0;
}
