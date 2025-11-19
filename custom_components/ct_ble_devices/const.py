"""Constants for ct_ble_devices."""
DOMAIN = "ct_ble_devices"
DEFAULT_NAME = "CT BLE Devices"

# 配置键
CONF_SCAN_INTERVAL = "scan_interval"
CONF_DEVICE_NAME_FILTER = "device_name_filter"
CONF_ENABLE_SCANNING = "enable_scanning"
CONF_SCAN_MODE = "scan_mode"  # 扫描模式：ha_bluetooth 或 direct_bleak

# 扫描模式
SCAN_MODE_HA_BLUETOOTH = "ha_bluetooth"  # 使用 Home Assistant 蓝牙集成（可能有节流）
SCAN_MODE_DIRECT_BLEAK = "direct_bleak"  # 直接使用 Bleak（无节流，捕获所有广播）

# 默认值
DEFAULT_SCAN_INTERVAL = 1  # 秒
DEFAULT_ENABLE_SCANNING = True
DEFAULT_SCAN_MODE = SCAN_MODE_DIRECT_BLEAK  # 默认使用直接扫描模式

