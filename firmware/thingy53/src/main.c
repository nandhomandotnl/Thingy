/*
 * Thingy:53 Bluetooth air mouse.
 *
 * The Thingy:53 shows up as a regular Bluetooth mouse (HID over GATT). The BMI270 gyroscope
 * moves the cursor, the two buttons are the left and right mouse buttons.
 *
 * LED: white flash at power-on = firmware started,
 *      blue blink = waiting for a computer, yellow = keep still (calibrating),
 *      short green blink = active, white blinks = forward direction changed,
 *      3x red at start-up = pairings erased.
 * Errors (blink forever, message repeated on the USB serial port):
 *      red = motion sensor, purple = Bluetooth, cyan = other start-up error.
 */

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <bluetooth/services/hids.h>

#include "airmouse.h"

LOG_MODULE_REGISTER(thingy_mouse, LOG_LEVEL_INF);

#define SAMPLE_RATE_HZ 100
#define CLICK_FREEZE_MS 150
#define COMBO_HOLD_MS 2000
#define RAD_TO_DEG 57.29578f

#define BASE_USB_HID_SPEC_VERSION 0x0101
#define INPUT_REP_MOUSE_ID 1
#define INPUT_REP_MOUSE_LEN 6
#define INPUT_REP_MOUSE_INDEX 0

#define BTN_LEFT BIT(0)
#define BTN_RIGHT BIT(1)

BT_HIDS_DEF(hids_obj, INPUT_REP_MOUSE_LEN);

static const struct gpio_dt_spec btn_left = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static const struct gpio_dt_spec btn_right = GPIO_DT_SPEC_GET(DT_ALIAS(sw1), gpios);
static const struct gpio_dt_spec led_red = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static const struct gpio_dt_spec led_green = GPIO_DT_SPEC_GET(DT_ALIAS(led1), gpios);
static const struct gpio_dt_spec led_blue = GPIO_DT_SPEC_GET(DT_ALIAS(led2), gpios);
static const struct device *const imu = DEVICE_DT_GET(DT_NODELABEL(bmi270));

static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_GAP_APPEARANCE,
		      (CONFIG_BT_DEVICE_APPEARANCE >> 0) & 0xff,
		      (CONFIG_BT_DEVICE_APPEARANCE >> 8) & 0xff),
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID16_ALL, BT_UUID_16_ENCODE(BT_UUID_HIDS_VAL)),
};

static const struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

/* Connection state, written from Bluetooth callbacks and read by the main loop. */
static K_MUTEX_DEFINE(conn_lock);
static struct bt_conn *current_conn;
static bool in_boot_mode;
static atomic_t recalibrate_req;

static struct k_work adv_work;

/* Persistent setting: which sensor axis points forward (see airmouse_cfg.forward). */
static int forward_step;

static struct airmouse air;

static const struct airmouse_cfg air_cfg = {
	.sample_rate_hz = SAMPLE_RATE_HZ,
	.sensitivity = 12.0f,
	.acceleration = 0.6f,
	.deadzone_dps = 2.0f,
	.smoothing = 0.3f,
	.forward = 0,
	.invert_x = true,
	.invert_y = true,
	.gravity_alpha = 0.02f,
	.calibration_samples = SAMPLE_RATE_HZ / 2,
	.still_threshold_dps = 4.0f,
	.still_samples = SAMPLE_RATE_HZ / 2,
	.bias_adapt_rate = 0.02f,
};

/* ---------- settings ---------- */

static int mouse_settings_set(const char *name, size_t len, settings_read_cb read_cb,
			      void *cb_arg)
{
	if (strcmp(name, "fwd") == 0 && len == sizeof(forward_step)) {
		return MIN(read_cb(cb_arg, &forward_step, sizeof(forward_step)), 0);
	}
	return -ENOENT;
}

SETTINGS_STATIC_HANDLER_DEFINE(thingy_mouse, "mouse", NULL, mouse_settings_set, NULL, NULL);

/* ---------- LEDs ---------- */

static void leds_set(bool r, bool g, bool b)
{
	gpio_pin_set_dt(&led_red, r);
	gpio_pin_set_dt(&led_green, g);
	gpio_pin_set_dt(&led_blue, b);
}

static void leds_blink(int count, bool r, bool g, bool b)
{
	for (int i = 0; i < count; i++) {
		leds_set(r, g, b);
		k_msleep(150);
		leds_set(false, false, false);
		k_msleep(150);
	}
}

/* Blink an error colour forever and keep logging why, so the cause can be read over USB
 * even when the serial port is opened after start-up.
 */
static void FUNC_NORETURN fail(const char *what, int err, bool r, bool g, bool b)
{
	for (uint32_t i = 0;; i++) {
		if (i % 4 == 0) {
			LOG_ERR("FOUT: %s (err %d)", what, err);
		}
		leds_set(r, g, b);
		k_msleep(250);
		leds_set(false, false, false);
		k_msleep(250);
	}
}

/* ---------- Bluetooth ---------- */

static void advertising_start(void)
{
	int err = bt_le_adv_start(BT_LE_ADV_CONN_FAST_1, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));

	if (err && err != -EALREADY) {
		LOG_ERR("FOUT: advertising start niet (err %d)", err);
		return;
	}
	LOG_INF("Advertising as \"%s\"", CONFIG_BT_DEVICE_NAME);
}

static void adv_work_handler(struct k_work *work)
{
	advertising_start();
}

static void connected(struct bt_conn *conn, uint8_t err)
{
	if (err) {
		LOG_WRN("Connection failed (err 0x%02x)", err);
		return;
	}

	LOG_INF("Connected");
	if (bt_hids_connected(&hids_obj, conn)) {
		LOG_ERR("Failed to notify HID service about connection");
	}

	k_mutex_lock(&conn_lock, K_FOREVER);
	if (!current_conn) {
		current_conn = bt_conn_ref(conn);
		in_boot_mode = false;
	}
	k_mutex_unlock(&conn_lock);
	atomic_set(&recalibrate_req, 1);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("Disconnected (reason 0x%02x)", reason);
	bt_hids_disconnected(&hids_obj, conn);

	k_mutex_lock(&conn_lock, K_FOREVER);
	if (current_conn == conn) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}
	k_mutex_unlock(&conn_lock);
}

static void recycled(void)
{
	/* The connection object is free again, so advertising can be restarted. */
	k_work_submit(&adv_work);
}

static void security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err)
{
	if (err) {
		LOG_WRN("Pairing failed (err %d)", err);
	} else {
		LOG_INF("Security level %u", level);
	}
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.recycled = recycled,
	.security_changed = security_changed,
};

static void hids_pm_evt_handler(enum bt_hids_pm_evt evt, struct bt_conn *conn)
{
	k_mutex_lock(&conn_lock, K_FOREVER);
	if (conn == current_conn) {
		in_boot_mode = (evt == BT_HIDS_PM_EVT_BOOT_MODE_ENTERED);
	}
	k_mutex_unlock(&conn_lock);
}

static int hid_init(void)
{
	static const uint8_t report_map[] = {
		0x05, 0x01,       /* Usage Page (Generic Desktop) */
		0x09, 0x02,       /* Usage (Mouse) */
		0xA1, 0x01,       /* Collection (Application) */
		0x85, INPUT_REP_MOUSE_ID, /* Report ID */
		0x09, 0x01,       /*   Usage (Pointer) */
		0xA1, 0x00,       /*   Collection (Physical) */
		0x05, 0x09,       /*     Usage Page (Buttons) */
		0x19, 0x01,       /*     Usage Minimum (1) */
		0x29, 0x03,       /*     Usage Maximum (3) */
		0x15, 0x00,       /*     Logical Minimum (0) */
		0x25, 0x01,       /*     Logical Maximum (1) */
		0x95, 0x03,       /*     Report Count (3) */
		0x75, 0x01,       /*     Report Size (1) */
		0x81, 0x02,       /*     Input (Data, Variable, Absolute) */
		0x95, 0x01,       /*     Report Count (1) */
		0x75, 0x05,       /*     Report Size (5) */
		0x81, 0x01,       /*     Input (Constant) padding */
		0x05, 0x01,       /*     Usage Page (Generic Desktop) */
		0x09, 0x30,       /*     Usage (X) */
		0x09, 0x31,       /*     Usage (Y) */
		0x16, 0x01, 0x80, /*     Logical Minimum (-32767) */
		0x26, 0xFF, 0x7F, /*     Logical Maximum (32767) */
		0x75, 0x10,       /*     Report Size (16) */
		0x95, 0x02,       /*     Report Count (2) */
		0x81, 0x06,       /*     Input (Data, Variable, Relative) */
		0x09, 0x38,       /*     Usage (Wheel) */
		0x15, 0x81,       /*     Logical Minimum (-127) */
		0x25, 0x7F,       /*     Logical Maximum (127) */
		0x75, 0x08,       /*     Report Size (8) */
		0x95, 0x01,       /*     Report Count (1) */
		0x81, 0x06,       /*     Input (Data, Variable, Relative) */
		0xC0,             /*   End Collection */
		0xC0,             /* End Collection */
	};
	struct bt_hids_init_param param = {0};

	param.rep_map.data = report_map;
	param.rep_map.size = sizeof(report_map);
	param.info.bcd_hid = BASE_USB_HID_SPEC_VERSION;
	param.info.b_country_code = 0x00;
	param.info.flags = BT_HIDS_REMOTE_WAKE | BT_HIDS_NORMALLY_CONNECTABLE;

	param.inp_rep_group_init.reports[0].size = INPUT_REP_MOUSE_LEN;
	param.inp_rep_group_init.reports[0].id = INPUT_REP_MOUSE_ID;
	param.inp_rep_group_init.cnt = 1;

	param.is_mouse = true;
	param.pm_evt_handler = hids_pm_evt_handler;

	return bt_hids_init(&hids_obj, &param);
}

/* Returns a referenced connection (or NULL); the caller must bt_conn_unref() it. */
static struct bt_conn *get_conn(bool *boot_mode)
{
	struct bt_conn *conn = NULL;

	k_mutex_lock(&conn_lock, K_FOREVER);
	if (current_conn) {
		conn = bt_conn_ref(current_conn);
		*boot_mode = in_boot_mode;
	}
	k_mutex_unlock(&conn_lock);
	return conn;
}

/* Sends one report; on success *dx and *dy are reduced by what was sent. */
static int send_report(struct bt_conn *conn, bool boot_mode, uint8_t buttons, int *dx, int *dy)
{
	int err;

	if (boot_mode) {
		int8_t x = CLAMP(*dx, INT8_MIN + 1, INT8_MAX);
		int8_t y = CLAMP(*dy, INT8_MIN + 1, INT8_MAX);

		err = bt_hids_boot_mouse_inp_rep_send(&hids_obj, conn, &buttons, x, y, NULL);
		if (!err) {
			*dx -= x;
			*dy -= y;
		}
		return err;
	}

	int16_t x = CLAMP(*dx, -32767, 32767);
	int16_t y = CLAMP(*dy, -32767, 32767);
	uint8_t rep[INPUT_REP_MOUSE_LEN];

	rep[0] = buttons;
	sys_put_le16(x, &rep[1]);
	sys_put_le16(y, &rep[3]);
	rep[5] = 0; /* wheel */

	err = bt_hids_inp_rep_send(&hids_obj, conn, INPUT_REP_MOUSE_INDEX, rep, sizeof(rep), NULL);
	if (!err) {
		*dx -= x;
		*dy -= y;
	}
	return err;
}

/* ---------- sensors and buttons ---------- */

static int imu_init(void)
{
	struct sensor_value full_scale = {.val1 = 500};
	struct sensor_value odr = {.val1 = SAMPLE_RATE_HZ};
	struct sensor_value acc_scale = {.val1 = 2};
	int err;

	if (!device_is_ready(imu)) {
		LOG_ERR("BMI270 not ready");
		return -ENODEV;
	}

	err = sensor_attr_set(imu, SENSOR_CHAN_GYRO_XYZ, SENSOR_ATTR_FULL_SCALE, &full_scale);
	err = err ?: sensor_attr_set(imu, SENSOR_CHAN_GYRO_XYZ, SENSOR_ATTR_SAMPLING_FREQUENCY, &odr);
	err = err ?: sensor_attr_set(imu, SENSOR_CHAN_ACCEL_XYZ, SENSOR_ATTR_FULL_SCALE, &acc_scale);
	err = err ?: sensor_attr_set(imu, SENSOR_CHAN_ACCEL_XYZ, SENSOR_ATTR_SAMPLING_FREQUENCY, &odr);
	if (err) {
		LOG_ERR("BMI270 configuration failed (err %d)", err);
	}
	return err;
}

static int imu_read(float gyro_dps[3], float accel[3])
{
	struct sensor_value g[3], a[3];
	int err = sensor_sample_fetch(imu);

	err = err ?: sensor_channel_get(imu, SENSOR_CHAN_GYRO_XYZ, g);
	err = err ?: sensor_channel_get(imu, SENSOR_CHAN_ACCEL_XYZ, a);
	if (err) {
		return err;
	}
	for (int i = 0; i < 3; i++) {
		gyro_dps[i] = sensor_value_to_float(&g[i]) * RAD_TO_DEG;
		accel[i] = sensor_value_to_float(&a[i]);
	}
	return 0;
}

static int gpio_init(void)
{
	const struct gpio_dt_spec *leds[] = {&led_red, &led_green, &led_blue};
	const struct gpio_dt_spec *btns[] = {&btn_left, &btn_right};
	int err = 0;

	for (size_t i = 0; i < ARRAY_SIZE(leds) && !err; i++) {
		err = gpio_pin_configure_dt(leds[i], GPIO_OUTPUT_INACTIVE);
	}
	for (size_t i = 0; i < ARRAY_SIZE(btns) && !err; i++) {
		err = gpio_pin_configure_dt(btns[i], GPIO_INPUT);
	}
	return err;
}

static uint8_t read_buttons(void)
{
	return (gpio_pin_get_dt(&btn_left) > 0 ? BTN_LEFT : 0) |
	       (gpio_pin_get_dt(&btn_right) > 0 ? BTN_RIGHT : 0);
}

static void apply_forward_step(void)
{
	struct airmouse_cfg cfg = air_cfg;

	cfg.forward = forward_step;
	airmouse_init(&air, &cfg);
}

/* ---------- main loop ---------- */

int main(void)
{
	int err;

	LOG_INF("Thingy:53 air mouse starting");

	err = gpio_init();
	if (err) {
		LOG_ERR("GPIO init failed (err %d)", err);
		return 0;
	}

	/* White flash: the firmware is running. */
	leds_set(true, true, true);
	k_msleep(500);
	leds_set(false, false, false);

	err = imu_init();
	if (err) {
		fail("bewegingssensor (BMI270) start niet", err, true, false, false);
	}

	k_work_init(&adv_work, adv_work_handler);

	err = hid_init();
	if (err) {
		fail("HID-service start niet", err, false, true, true);
	}

	/* Blue while the Bluetooth core starts; stays blue if it hangs. */
	leds_set(false, false, true);
	err = bt_enable(NULL);
	leds_set(false, false, false);
	if (err) {
		fail("Bluetooth (netwerkkern) start niet", err, true, false, true);
	}
	LOG_INF("Bluetooth started");
	settings_load();
	apply_forward_step();

	/* Hold the left button while switching on to forget all paired computers. */
	if (read_buttons() & BTN_LEFT) {
		LOG_INF("Erasing all pairings");
		bt_unpair(BT_ID_DEFAULT, BT_ADDR_LE_ANY);
		leds_blink(3, true, false, false);
		while (read_buttons() & BTN_LEFT) {
			k_msleep(10);
		}
	}

	advertising_start();

	uint32_t tick = 0;
	int64_t frozen_until = 0;
	int64_t combo_since = 0;
	bool combo_active = false;
	bool was_calibrated = false;
	uint8_t sent_buttons = 0;
	int pending_dx = 0, pending_dy = 0;

	while (true) {
		k_msleep(1000 / SAMPLE_RATE_HZ);
		tick++;

		bool boot_mode = false;
		struct bt_conn *conn = get_conn(&boot_mode);
		int64_t now = k_uptime_get();

		if (!conn) {
			/* Waiting for a computer: clear blue blink every second. */
			leds_set(false, false, (tick % SAMPLE_RATE_HZ) < 25);
			if (tick % (5 * SAMPLE_RATE_HZ) == 0) {
				LOG_INF("Wacht op koppelen als \"%s\"", CONFIG_BT_DEVICE_NAME);
			}
			sent_buttons = 0;
			pending_dx = pending_dy = 0;
			continue;
		}

		if (atomic_cas(&recalibrate_req, 1, 0)) {
			airmouse_recalibrate(&air);
			was_calibrated = false;
		}

		/* Buttons. Both held for 2 s: turn the "forward" direction 90 degrees. */
		uint8_t buttons = read_buttons();

		if (buttons == (BTN_LEFT | BTN_RIGHT)) {
			if (!combo_active) {
				combo_active = true;
				combo_since = now;
			} else if (combo_since && now - combo_since >= COMBO_HOLD_MS) {
				forward_step = (forward_step + 1) % 4;
				settings_save_one("mouse/fwd", &forward_step, sizeof(forward_step));
				LOG_INF("Forward direction step %d", forward_step);
				leds_blink(forward_step + 1, true, true, true);
				apply_forward_step();
				was_calibrated = false;
				combo_since = 0; /* once per hold */
			}
		} else if (buttons == 0) {
			combo_active = false;
		}
		if (combo_active) {
			buttons = 0; /* no clicks while using the combo */
		}
		if (buttons != sent_buttons) {
			/* Pressing a button nudges the device; freeze briefly so the click lands. */
			frozen_until = now + CLICK_FREEZE_MS;
			airmouse_reset_motion(&air);
		}

		/* Motion. */
		float gyro[3], accel[3];
		int dx = 0, dy = 0;

		if (imu_read(gyro, accel) == 0) {
			airmouse_update(&air, gyro, accel, &dx, &dy);
		}

		bool calibrated = airmouse_calibrated(&air);

		if (calibrated && !was_calibrated) {
			LOG_INF("Calibrated, bias %.2f %.2f %.2f deg/s",
				(double)air.bias[0], (double)air.bias[1], (double)air.bias[2]);
		}
		was_calibrated = calibrated;

		if (now >= frozen_until) {
			pending_dx += dx;
			pending_dy += dy;
		}

		if (pending_dx || pending_dy || buttons != sent_buttons) {
			err = send_report(conn, boot_mode, buttons, &pending_dx, &pending_dy);
			if (!err) {
				sent_buttons = buttons;
			} else {
				/* Not paired yet or buffers full: keep at most a little movement. */
				pending_dx = CLAMP(pending_dx, -200, 200);
				pending_dy = CLAMP(pending_dy, -200, 200);
			}
		}

		/* LED: yellow while calibrating, short green blink every 3 s when active. */
		if (!calibrated) {
			leds_set(true, true, false);
		} else {
			leds_set(false, (tick % (3 * SAMPLE_RATE_HZ)) < 3, false);
		}

		bt_conn_unref(conn);
	}

	return 0;
}
