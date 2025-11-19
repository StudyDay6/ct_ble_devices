"""Config flow for CT BLE Devices."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_ENABLE_SCANNING,
    DEFAULT_SCAN_MODE,
    CONF_SCAN_INTERVAL,
    CONF_DEVICE_NAME_FILTER,
    CONF_ENABLE_SCANNING,
    CONF_SCAN_MODE,
    SCAN_MODE_HA_BLUETOOTH,
    SCAN_MODE_DIRECT_BLEAK,
)


class CTBLEDevicesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CT BLE Devices."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # 检查是否已经配置过（如果需要单实例）
            # await self.async_set_unique_id(DOMAIN)
            # self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=DEFAULT_NAME,
                data=user_input,
            )

        data_schema = {
            vol.Required(CONF_ENABLE_SCANNING, default=DEFAULT_ENABLE_SCANNING): bool,
            vol.Required(CONF_SCAN_MODE, default=DEFAULT_SCAN_MODE): vol.In({
                SCAN_MODE_DIRECT_BLEAK: "直接 Bleak 扫描（无节流，捕获所有广播）",
                SCAN_MODE_HA_BLUETOOTH: "Home Assistant 蓝牙集成（可能有节流）",
            }),
            vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=300)
            ),
            vol.Optional(CONF_DEVICE_NAME_FILTER, default=""): str,
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(data_schema),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return CTBLEDevicesOptionsFlow(config_entry)


class CTBLEDevicesOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for CT BLE Devices."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENABLE_SCANNING,
                        default=self.config_entry.options.get(
                            CONF_ENABLE_SCANNING, DEFAULT_ENABLE_SCANNING
                        ),
                    ): bool,
                    vol.Required(
                        CONF_SCAN_MODE,
                        default=self.config_entry.options.get(
                            CONF_SCAN_MODE, DEFAULT_SCAN_MODE
                        ),
                    ): vol.In({
                        SCAN_MODE_DIRECT_BLEAK: "直接 Bleak 扫描（无节流，捕获所有广播）",
                        SCAN_MODE_HA_BLUETOOTH: "Home Assistant 蓝牙集成（可能有节流）",
                    }),
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
                    vol.Optional(
                        CONF_DEVICE_NAME_FILTER,
                        default=self.config_entry.options.get(CONF_DEVICE_NAME_FILTER, ""),
                    ): str,
                }
            ),
        )

