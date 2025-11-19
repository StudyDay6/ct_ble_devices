"""CT BLE Devices Integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .scanner import BLEScanner

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ct_ble_devices from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    # 启动自动更新（如果启用）
    from .updater import async_setup_auto_update
    updater = await async_setup_auto_update(hass, entry)
    if updater:
        hass.data[DOMAIN]["updater"] = updater
    
    # 创建扫描器实例
    scanner = BLEScanner(hass, entry)
    await scanner.async_setup()
    
    hass.data[DOMAIN][entry.entry_id] = scanner
    
    # 设置传感器平台
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    scanner = hass.data[DOMAIN].get(entry.entry_id)
    if scanner:
        await scanner.async_stop()
    
    # 停止自动更新
    updater = hass.data[DOMAIN].get("updater")
    if updater:
        updater.stop()
        hass.data[DOMAIN].pop("updater")
    
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok

