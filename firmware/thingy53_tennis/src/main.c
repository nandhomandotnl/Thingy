/*
 * Thingy:53 tennis racket sensor.
 *
 * Streams the BMI270 accelerometer + gyroscope over Bluetooth LE to a receiver (laptop or
 * Raspberry Pi running tennis_tracker). No pairing is needed.
 *
 * GATT service 7e510001-0c1a-4b7e-9a35-54656e6e6973 ("Tennis"):
 *   7e510002 data    notify  packets of IMU samples, see "packet format" below
 *   7e510003 event   notify  [type=2][code][t_us u32]  code 1 = button pressed
 *   7e510004 control write   [0x01] identify (blink white)
 *                            [0x02][rate_hz u16] set sample rate (50, 100, 200 or 400)
 *   7e510005 info    read    [proto=1][rate_hz u16][accel_range_g u8][gyro_range_dps u16]
 *
 * Packet format (little endian):
 *   [type=1 u8][n u8][seq u16][t_us u32 of first sample][rate_hz u16]
 *   then n samples of [ax ay az gx gy gz] int16; accel in g/2048, gyro in dps/16.384
 *
 * LED: blue blink = waiting for a receiver, green blink = connected,
 *      white = identify, red blinking = sensor error.
 */

#include <errno.h>
#include <stdio.h>
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

LOG_MODULE_REGISTER(tennis, LOG_LEVEL_INF);

#define PROTO_VERSION 1
#define DEFAULT_RATE_HZ 200
#define ACCEL_RANGE_G 16
#define GYRO_RANGE_DPS 2000
#define ACCEL_LSB_PER_G (32768.0f / ACCEL_RANGE_G)
#define GYRO_LSB_PER_DPS (32768.0f / GYRO_RANGE_DPS)
#define RAD_TO_DEG 57.29578f
#define STANDARD_GRAVITY 9.80665f

#define HEADER_LEN 10
#define SAMPLE_LEN 12
#define MAX_SAMPLES_PER_PACKET 10

#define TENNIS_UUID(x) BT_UUID_128_ENCODE(0x7e510000 + (x), 0x0c1a, 0x4b7e, 0x9a35, 0x54656e6e6973)

static const struct bt_uuid_128 svc_uuid = BT_UUID_INIT_128(TENNIS_UUID(1));
static const struct bt_uuid_128 data_uuid = BT_UUID_INIT_128(TENNIS_UUID(2));
static const struct bt_uuid_128 event_uuid = BT_UUID_INIT_128(TENNIS_UUID(3));
static const struct bt_uuid_128 ctrl_uuid = BT_UUID_INIT_128(TENNIS_UUID(4));
static const struct bt_uuid_128 info_uuid = BT_UUID_INIT_128(TENNIS_UUID(5));

static const struct gpio_dt_spec button = GPIO_DT_SPEC_GET(DT_ALIAS(sw0), gpios);
static const struct gpio_dt_spec led_red = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static const struct gpio_dt_spec led_green = GPIO_DT_SPEC_GET(DT_ALIAS(led1), gpios);
static const struct gpio_dt_spec led_blue = GPIO_DT_SPEC_GET(DT_ALIAS(led2), gpios);
static const struct device *const imu = DEVICE_DT_GET(DT_NODELABEL(bmi270));

static char device_name[24];

static struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, TENNIS_UUID(1)),
};

static struct bt_data sd[] = {
	BT_DATA(BT_DATA_NAME_COMPLETE, device_name, 0),
};

static K_MUTEX_DEFINE(conn_lock);
static struct bt_conn *current_conn;
static atomic_t streaming;
static atomic_t event_subscribed;
static atomic_t requested_rate;
static atomic_t identify_until_ms;
static int sample_rate_hz = DEFAULT_RATE_HZ;

static struct k_work adv_work;
static K_SEM_DEFINE(sample_sem, 0, 1);

static void sample_timer_fn(struct k_timer *timer)
{
	k_sem_give(&sample_sem);
}

static K_TIMER_DEFINE(sample_timer, sample_timer_fn, NULL);

/* ---------- GATT service ---------- */

static void data_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	bool on = value == BT_GATT_CCC_NOTIFY;

	atomic_set(&streaming, on);
	if (on) {
		k_timer_start(&sample_timer, K_USEC(1000000 / sample_rate_hz),
			      K_USEC(1000000 / sample_rate_hz));
	} else {
		k_timer_stop(&sample_timer);
	}
	LOG_INF("Streaming %s", on ? "started" : "stopped");
}

static void event_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	atomic_set(&event_subscribed, value == BT_GATT_CCC_NOTIFY);
}

static ssize_t ctrl_write(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf,
			  uint16_t len, uint16_t offset, uint8_t flags)
{
	const uint8_t *cmd = buf;

	if (offset != 0 || len < 1) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
	}

	switch (cmd[0]) {
	case 0x01:
		atomic_set(&identify_until_ms, (atomic_val_t)(k_uptime_get() + 3000));
		return len;
	case 0x02:
		if (len != 3) {
			return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
		}
		uint16_t rate = sys_get_le16(&cmd[1]);

		if (rate != 50 && rate != 100 && rate != 200 && rate != 400) {
			return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
		}
		atomic_set(&requested_rate, rate);
		return len;
	default:
		return BT_GATT_ERR(BT_ATT_ERR_NOT_SUPPORTED);
	}
}

static ssize_t info_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf,
			 uint16_t len, uint16_t offset)
{
	uint8_t info[6];

	info[0] = PROTO_VERSION;
	sys_put_le16(sample_rate_hz, &info[1]);
	info[3] = ACCEL_RANGE_G;
	sys_put_le16(GYRO_RANGE_DPS, &info[4]);

	return bt_gatt_attr_read(conn, attr, buf, len, offset, info, sizeof(info));
}

BT_GATT_SERVICE_DEFINE(tennis_svc,
	BT_GATT_PRIMARY_SERVICE(&svc_uuid),
	BT_GATT_CHARACTERISTIC(&data_uuid.uuid, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE,
			       NULL, NULL, NULL),
	BT_GATT_CCC(data_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(&event_uuid.uuid, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE,
			       NULL, NULL, NULL),
	BT_GATT_CCC(event_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(&ctrl_uuid.uuid,
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
			       BT_GATT_PERM_WRITE, NULL, ctrl_write, NULL),
	BT_GATT_CHARACTERISTIC(&info_uuid.uuid, BT_GATT_CHRC_READ, BT_GATT_PERM_READ,
			       info_read, NULL, NULL),
);

#define DATA_ATTR (&tennis_svc.attrs[2])
#define EVENT_ATTR (&tennis_svc.attrs[5])

/* ---------- connection handling ---------- */

static void advertising_start(void)
{
	int err = bt_le_adv_start(BT_LE_ADV_CONN_FAST_2, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));

	if (err && err != -EALREADY) {
		LOG_ERR("FOUT: advertising start niet (err %d)", err);
		return;
	}
	LOG_INF("Advertising as \"%s\"", device_name);
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

	k_mutex_lock(&conn_lock, K_FOREVER);
	if (!current_conn) {
		current_conn = bt_conn_ref(conn);
	}
	k_mutex_unlock(&conn_lock);

	/* Faster radio and bigger packets; the receiver may refuse, which is fine. */
	bt_conn_le_phy_update(conn, BT_CONN_LE_PHY_PARAM_2M);
	bt_conn_le_data_len_update(conn, BT_LE_DATA_LEN_PARAM_MAX);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	LOG_INF("Disconnected (reason 0x%02x)", reason);

	k_mutex_lock(&conn_lock, K_FOREVER);
	if (current_conn == conn) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
		atomic_set(&streaming, 0);
		atomic_set(&event_subscribed, 0);
		k_timer_stop(&sample_timer);
	}
	k_mutex_unlock(&conn_lock);
}

static void recycled(void)
{
	k_work_submit(&adv_work);
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
	.connected = connected,
	.disconnected = disconnected,
	.recycled = recycled,
};

static struct bt_conn *get_conn(void)
{
	struct bt_conn *conn = NULL;

	k_mutex_lock(&conn_lock, K_FOREVER);
	if (current_conn) {
		conn = bt_conn_ref(current_conn);
	}
	k_mutex_unlock(&conn_lock);
	return conn;
}

/* ---------- sensor ---------- */

static int imu_set_rate(int rate_hz)
{
	struct sensor_value odr = {.val1 = rate_hz};
	int err;

	err = sensor_attr_set(imu, SENSOR_CHAN_GYRO_XYZ, SENSOR_ATTR_SAMPLING_FREQUENCY, &odr);
	err = err ?: sensor_attr_set(imu, SENSOR_CHAN_ACCEL_XYZ, SENSOR_ATTR_SAMPLING_FREQUENCY,
				     &odr);
	return err;
}

static int imu_init(void)
{
	struct sensor_value gyro_range = {.val1 = GYRO_RANGE_DPS};
	struct sensor_value acc_range = {.val1 = ACCEL_RANGE_G};
	int err;

	if (!device_is_ready(imu)) {
		return -ENODEV;
	}
	err = sensor_attr_set(imu, SENSOR_CHAN_GYRO_XYZ, SENSOR_ATTR_FULL_SCALE, &gyro_range);
	err = err ?: sensor_attr_set(imu, SENSOR_CHAN_ACCEL_XYZ, SENSOR_ATTR_FULL_SCALE,
				     &acc_range);
	err = err ?: imu_set_rate(DEFAULT_RATE_HZ);
	return err;
}

static int16_t to_i16(float v)
{
	return (int16_t)CLAMP(v >= 0 ? v + 0.5f : v - 0.5f, -32767.0f, 32767.0f);
}

/* Reads one sample into 12 bytes: accel (g/2048) then gyro (dps/16.384). */
static int imu_read_packed(uint8_t *out)
{
	struct sensor_value a[3], g[3];
	int err = sensor_sample_fetch(imu);

	err = err ?: sensor_channel_get(imu, SENSOR_CHAN_ACCEL_XYZ, a);
	err = err ?: sensor_channel_get(imu, SENSOR_CHAN_GYRO_XYZ, g);
	if (err) {
		return err;
	}
	for (int i = 0; i < 3; i++) {
		float acc_g = sensor_value_to_float(&a[i]) / STANDARD_GRAVITY;
		float gyro_dps = sensor_value_to_float(&g[i]) * RAD_TO_DEG;

		sys_put_le16(to_i16(acc_g * ACCEL_LSB_PER_G), &out[2 * i]);
		sys_put_le16(to_i16(gyro_dps * GYRO_LSB_PER_DPS), &out[6 + 2 * i]);
	}
	return 0;
}

static uint32_t now_us(void)
{
	return (uint32_t)k_ticks_to_us_floor64(k_uptime_ticks());
}

/* ---------- streaming thread ---------- */

static void stream_thread(void *p1, void *p2, void *p3)
{
	static uint8_t pkt[HEADER_LEN + MAX_SAMPLES_PER_PACKET * SAMPLE_LEN];
	uint16_t seq = 0;
	int n = 0;
	uint32_t dropped = 0;

	while (true) {
		k_sem_take(&sample_sem, K_FOREVER);

		if (!atomic_get(&streaming)) {
			n = 0;
			continue;
		}

		atomic_val_t rate = atomic_set(&requested_rate, 0);

		if (rate && rate != sample_rate_hz) {
			if (imu_set_rate(rate) == 0) {
				sample_rate_hz = rate;
				k_timer_start(&sample_timer, K_USEC(1000000 / rate),
					      K_USEC(1000000 / rate));
				LOG_INF("Sample rate %d Hz", (int)rate);
			}
			n = 0;
		}

		struct bt_conn *conn = get_conn();

		if (!conn) {
			continue;
		}

		/* Samples per packet depend on the negotiated ATT MTU. */
		int max_n = MIN(MAX_SAMPLES_PER_PACKET,
				((int)bt_gatt_get_mtu(conn) - 3 - HEADER_LEN) / SAMPLE_LEN);

		max_n = MAX(max_n, 1);

		if (n == 0) {
			pkt[0] = 1;
			sys_put_le32(now_us(), &pkt[4]);
			sys_put_le16(sample_rate_hz, &pkt[8]);
		}
		if (imu_read_packed(&pkt[HEADER_LEN + n * SAMPLE_LEN]) == 0) {
			n++;
		}

		if (n >= max_n) {
			pkt[1] = n;
			sys_put_le16(seq, &pkt[2]);
			int err = bt_gatt_notify(conn, DATA_ATTR, pkt, HEADER_LEN + n * SAMPLE_LEN);

			if (err) {
				/* Buffers full: drop; the receiver sees the gap in seq. */
				if ((dropped++ % 100) == 0) {
					LOG_WRN("Packet dropped (err %d, total %u)", err, dropped);
				}
			}
			seq++;
			n = 0;
		}
		bt_conn_unref(conn);
	}
}

K_THREAD_DEFINE(stream_tid, 2048, stream_thread, NULL, NULL, NULL, 5, 0, 0);

/* ---------- main: LEDs and button ---------- */

static void leds_set(bool r, bool g, bool b)
{
	gpio_pin_set_dt(&led_red, r);
	gpio_pin_set_dt(&led_green, g);
	gpio_pin_set_dt(&led_blue, b);
}

static void send_button_event(void)
{
	struct bt_conn *conn = get_conn();
	uint8_t ev[6] = {2, 1};

	if (!conn) {
		return;
	}
	sys_put_le32(now_us(), &ev[2]);
	if (atomic_get(&event_subscribed)) {
		bt_gatt_notify(conn, EVENT_ATTR, ev, sizeof(ev));
	}
	bt_conn_unref(conn);
}

static void make_device_name(void)
{
	bt_addr_le_t addrs[1];
	size_t count = ARRAY_SIZE(addrs);

	bt_id_get(addrs, &count);
	if (count > 0) {
		snprintf(device_name, sizeof(device_name), "Tennis-%02X%02X",
			 addrs[0].a.val[1], addrs[0].a.val[0]);
	} else {
		strcpy(device_name, "Tennis-Thingy");
	}
	bt_set_name(device_name);
	sd[0].data_len = strlen(device_name);
}

int main(void)
{
	const struct gpio_dt_spec *leds[] = {&led_red, &led_green, &led_blue};
	int err = 0;

	LOG_INF("Thingy:53 tennis sensor starting");

	for (size_t i = 0; i < ARRAY_SIZE(leds) && !err; i++) {
		err = gpio_pin_configure_dt(leds[i], GPIO_OUTPUT_INACTIVE);
	}
	err = err ?: gpio_pin_configure_dt(&button, GPIO_INPUT);
	if (err) {
		LOG_ERR("GPIO init failed (err %d)", err);
		return 0;
	}

	leds_set(true, true, true);
	k_msleep(300);
	leds_set(false, false, false);

	err = imu_init();
	while (err) {
		LOG_ERR("FOUT: bewegingssensor (BMI270) start niet (err %d)", err);
		leds_set(true, false, false);
		k_msleep(250);
		leds_set(false, false, false);
		k_msleep(250);
	}

	k_work_init(&adv_work, adv_work_handler);

	err = bt_enable(NULL);
	while (err) {
		LOG_ERR("FOUT: Bluetooth start niet (err %d)", err);
		leds_set(true, false, true);
		k_msleep(250);
		leds_set(false, false, false);
		k_msleep(250);
	}
	if (IS_ENABLED(CONFIG_SETTINGS)) {
		settings_load();
	}

	make_device_name();
	advertising_start();

	bool last_button = false;

	for (uint32_t tick = 0;; tick++) {
		k_msleep(50);

		bool pressed = gpio_pin_get_dt(&button) > 0;

		if (pressed && !last_button) {
			LOG_INF("Button marker");
			send_button_event();
		}
		last_button = pressed;

		struct bt_conn *conn = get_conn();
		bool connected_now = conn != NULL;

		if (conn) {
			bt_conn_unref(conn);
		}

		if (k_uptime_get() < (int64_t)atomic_get(&identify_until_ms)) {
			bool on = (tick % 4) < 2;

			leds_set(on, on, on);
		} else if (pressed) {
			leds_set(false, true, true);
		} else if (!connected_now) {
			leds_set(false, false, (tick % 20) < 3);      /* blue blink, waiting */
		} else {
			leds_set(false, (tick % 40) < 2, false);      /* short green, connected */
		}
	}

	return 0;
}
