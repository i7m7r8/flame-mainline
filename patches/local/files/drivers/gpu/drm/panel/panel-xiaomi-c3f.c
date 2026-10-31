// SPDX-License-Identifier: GPL-2.0-only
/*
 * Xiaomi c3f 720x1640 DSI video mode panel (Redmi 14C 5G / POCO M7 5G).
 *
 * Init and power-down sequences are the vendor ones, converted from this
 * device's own device tree by tools/gen_panel_init.py. Brightness goes over
 * DCS 0x51 (vendor bl_ctrl_dcs, 12-bit, minimum level 7), so prepare() brings
 * it up at full scale and no external backlight is described.
 */

#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>

#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>
#include <drm/drm_probe_helper.h>

#include "panel-xiaomi-c3f-init.h"

#define C3F_BL_MAX	4095	/* vendor qcom,mdss-dsi-bl-max-level */

struct c3f {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi;
	struct regulator_bulk_data supplies[4];
	struct gpio_desc *reset;
	enum drm_panel_orientation orientation;
};

static inline struct c3f *panel_to_c3f(struct drm_panel *panel)
{
	return container_of(panel, struct c3f, panel);
}

static void c3f_send_cmds(struct mipi_dsi_device *dsi,
			  const struct c3f_cmd *cmds, size_t count)
{
	size_t i;

	for (i = 0; i < count; i++) {
		const struct c3f_cmd *cmd = &cmds[i];
		int ret;

		if (cmd->type == C3F_DELAY) {
			msleep(cmd->delay);
			continue;
		}

		if (cmd->len <= 1)
			ret = mipi_dsi_dcs_write(dsi, cmd->data[0], NULL, 0);
		else
			ret = mipi_dsi_dcs_write(dsi, cmd->data[0],
						 cmd->data + 1, cmd->len - 1);
		if (ret < 0)
			dev_err(&dsi->dev, "cmd %zu (0x%02x) failed: %d\n",
				i, cmd->data[0], ret);
	}
}

static int c3f_prepare(struct drm_panel *panel)
{
	struct c3f *ctx = panel_to_c3f(panel);
	struct mipi_dsi_device *dsi = ctx->dsi;
	int ret;

	ret = regulator_bulk_enable(ARRAY_SIZE(ctx->supplies), ctx->supplies);
	if (ret)
		return ret;

	/*
	 * Vendor reset sequence is raw levels <0 5> <1 5> <0 5> <1 10>; with
	 * reset-gpios = GPIO_ACTIVE_LOW the consumer values are inverted.
	 */
	if (ctx->reset) {
		gpiod_set_value_cansleep(ctx->reset, 1);
		usleep_range(5000, 6000);
		gpiod_set_value_cansleep(ctx->reset, 0);
		usleep_range(5000, 6000);
		gpiod_set_value_cansleep(ctx->reset, 1);
		usleep_range(5000, 6000);
		gpiod_set_value_cansleep(ctx->reset, 0);
		usleep_range(10000, 11000);
	}

	c3f_send_cmds(dsi, c3f_init_cmds, C3F_INIT_CMDS_COUNT);

	ret = mipi_dsi_dcs_set_display_brightness(dsi, C3F_BL_MAX);
	if (ret < 0)
		dev_err(&dsi->dev, "failed to set backlight: %d\n", ret);

	return 0;
}

static int c3f_enable(struct drm_panel *panel)
{
	struct c3f *ctx = panel_to_c3f(panel);

	mipi_dsi_dcs_set_display_on(ctx->dsi);
	/* vendor waits 100 ms after the final display-on of the on sequence */
	msleep(100);

	return 0;
}

static int c3f_disable(struct drm_panel *panel)
{
	struct c3f *ctx = panel_to_c3f(panel);

	c3f_send_cmds(ctx->dsi, c3f_off_cmds, C3F_OFF_CMDS_COUNT);

	return 0;
}

static int c3f_unprepare(struct drm_panel *panel)
{
	struct c3f *ctx = panel_to_c3f(panel);

	if (ctx->reset)
		gpiod_set_value_cansleep(ctx->reset, 1);

	regulator_bulk_disable(ARRAY_SIZE(ctx->supplies), ctx->supplies);

	return 0;
}

static const struct drm_display_mode c3f_mode = {
	/* 720x1640@120 from the vendor timing: h 720+25+3+89, v 1640+331+2+20 */
	.clock = 200177,
	.hdisplay = 720,
	.hsync_start = 720 + 25,
	.hsync_end = 720 + 25 + 3,
	.htotal = 720 + 25 + 3 + 89,
	.vdisplay = 1640,
	.vsync_start = 1640 + 331,
	.vsync_end = 1640 + 331 + 2,
	.vtotal = 1640 + 331 + 2 + 20,
	.width_mm = 70,
	.height_mm = 160,
	.type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED,
};

static int c3f_get_modes(struct drm_panel *panel,
			 struct drm_connector *connector)
{
	return drm_connector_helper_get_modes_fixed(connector, &c3f_mode);
}

static enum drm_panel_orientation c3f_get_orientation(struct drm_panel *panel)
{
	struct c3f *ctx = panel_to_c3f(panel);

	return ctx->orientation;
}

static const struct drm_panel_funcs c3f_funcs = {
	.prepare = c3f_prepare,
	.enable = c3f_enable,
	.disable = c3f_disable,
	.unprepare = c3f_unprepare,
	.get_modes = c3f_get_modes,
	.get_orientation = c3f_get_orientation,
};

static int c3f_probe(struct mipi_dsi_device *dsi)
{
	struct c3f *ctx;
	int ret;

	ctx = devm_drm_panel_alloc(&dsi->dev, struct c3f, panel,
				   &c3f_funcs, DRM_MODE_CONNECTOR_DSI);
	if (IS_ERR(ctx))
		return PTR_ERR(ctx);

	mipi_dsi_set_drvdata(dsi, ctx);
	ctx->dsi = dsi;

	ctx->supplies[0].supply = "vddio";
	ctx->supplies[1].supply = "lab";
	ctx->supplies[2].supply = "ibb";
	ctx->supplies[3].supply = "extdvdd";

	ret = devm_regulator_bulk_get(&dsi->dev, ARRAY_SIZE(ctx->supplies),
				      ctx->supplies);
	if (ret)
		return dev_err_probe(&dsi->dev, ret, "failed to get supplies\n");

	ctx->reset = devm_gpiod_get_optional(&dsi->dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(ctx->reset))
		return dev_err_probe(&dsi->dev, PTR_ERR(ctx->reset),
				     "failed to get reset GPIO\n");

	ret = of_drm_get_panel_orientation(dsi->dev.of_node, &ctx->orientation);
	if (ret)
		return dev_err_probe(&dsi->dev, ret,
				     "failed to get panel orientation\n");

	/* the DSI host must be powered (LP-11) before the init commands */
	ctx->panel.prepare_prev_first = true;

	drm_panel_add(&ctx->panel);

	dsi->lanes = 4;
	dsi->format = MIPI_DSI_FMT_RGB888;
	/* vendor: dsi_video_mode, non_burst_sync_event, LP command mode */
	dsi->mode_flags = MIPI_DSI_MODE_VIDEO | MIPI_DSI_MODE_LPM;

	return devm_mipi_dsi_attach(&dsi->dev, dsi);
}

static const struct of_device_id c3f_of_match[] = {
	{ .compatible = "xiaomi,c3f-720x1640-video" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, c3f_of_match);

static struct mipi_dsi_driver c3f_driver = {
	.probe = c3f_probe,
	.driver = {
		.name = "panel-xiaomi-c3f",
		.of_match_table = c3f_of_match,
	},
};
module_mipi_dsi_driver(c3f_driver);

MODULE_DESCRIPTION("Xiaomi c3f 720x1640 DSI video mode panel");
MODULE_LICENSE("GPL");
