"""Bluetooth scanner for CT BLE Devices."""
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    BluetoothChange,
    BluetoothScanningMode,
    async_register_callback,
)
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher

from .const import (
    CONF_ENABLE_SCANNING,
    DEFAULT_ENABLE_SCANNING,
)

_LOGGER = logging.getLogger(__name__)


class BLEScanner:
    """Bluetooth Low Energy scanner."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the scanner."""
        self.hass = hass
        self.entry = entry
        self._devices: Dict[str, Dict] = {}
        self._scanning = False
        self._cancel_bt_cb: Optional[Callable[[], None]] = None
        self._update_callbacks: list[Callable[[], None]] = []
        # 实体创建回调：用于通知sensor平台创建/更新实体
        self._entity_callbacks: list[Callable[[Dict], None]] = []
        # 扫描重启定时器取消回调
        self._restart_scan_cancel: Optional[Callable[[], None]] = None
        # 存储每个设备上次发送的数据签名（用于去重，排除RSSI和时间戳）
        self._last_sent_data_hash: Dict[str, str] = {}

    @property
    def devices(self) -> Dict[str, Dict]:
        """Return discovered devices."""
        return self._devices
    
    def _get_data_signature(self, device_info: Dict) -> str:
        """生成数据签名（排除时间戳和RSSI，用于去重）.
        
        Args:
            device_info: 设备信息字典
            
        Returns:
            数据签名的 MD5 哈希值（十六进制字符串）
        """
        # 复制数据，排除时间戳和RSSI
        signature_data = {
            "address": device_info.get("address"),
            "name": device_info.get("name"),
            "manufacturer_data": device_info.get("manufacturer_data"),
            "service_data": device_info.get("service_data"),
            "service_uuids": sorted(device_info.get("service_uuids", [])) if device_info.get("service_uuids") else [],
            "tx_power": device_info.get("tx_power"),
            "source": device_info.get("source"),
        }
        
        # 将 bytes 数据转换为十六进制字符串以便序列化
        def convert_bytes(obj):
            if isinstance(obj, bytes):
                return obj.hex()
            elif isinstance(obj, dict):
                return {k: convert_bytes(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_bytes(item) for item in obj]
            return obj
        
        # 转换并生成 JSON 字符串
        converted_data = convert_bytes(signature_data)
        json_str = json.dumps(converted_data, sort_keys=True)
        
        # 生成 MD5 哈希
        return hashlib.md5(json_str.encode()).hexdigest()

    async def async_setup(self) -> None:
        """Set up the scanner."""
        if not self.entry.options.get(CONF_ENABLE_SCANNING, DEFAULT_ENABLE_SCANNING):
            _LOGGER.info("BLE scanning is disabled")
            return

        await self._start_scanning()

    async def _start_scanning(self) -> None:
        """Start scanning."""
        if self._scanning:
            return

        self._scanning = True
        await self._start_ha_bluetooth_scanning()
        # 启动扫描重启定时器（每4秒执行一次）
        self._restart_scan_cancel = async_track_time_interval(
            self.hass,
            self._restart_scan_periodically,
            timedelta(seconds=4),
            name="ct_ble_devices_restart_scan",
        )


    async def _start_ha_bluetooth_scanning(self) -> None:
        """Start scanning using HA bluetooth callbacks - no filtering."""
        @callback
        def _bt_callback(service_info: BluetoothServiceInfoBleak, change: BluetoothChange) -> None:
            """Callback for all BLE advertisements - filter by device name prefix."""
            if change != BluetoothChange.ADVERTISEMENT:
                return
            
            # 获取设备名称
            name = service_info.name or service_info.advertisement.local_name or ""
            
            # 只处理名称前缀为 "Gait" 的设备
            if not name.startswith("Gait"):
                return
            
            # 构建设备信息
            device_info = {
                "address": service_info.address,
                "name": name,
                "rssi": service_info.rssi,
                "manufacturer_data": dict(service_info.manufacturer_data or {}),
                "service_data": dict(service_info.service_data or {}),
                "service_uuids": list(service_info.service_uuids or []),
                "tx_power": getattr(service_info, "tx_power", None),
                "source": service_info.source,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "time_unix": time.time(),
            }
            
            # 更新设备信息
            is_new_device = service_info.address not in self._devices
            self._devices[service_info.address] = device_info
            
            # 通知实体平台创建或更新实体
            for callback_func in self._entity_callbacks:
                try:
                    callback_func(device_info)
                except Exception as e:
                    _LOGGER.error("执行实体回调时出错: %s", e, exc_info=True)
            
            if is_new_device:
                _LOGGER.info("发现新 Gait 设备: %s (地址: %s)", name, service_info.address)
            
            # 去重检查：生成数据签名（排除RSSI和时间戳）
            current_hash = self._get_data_signature(device_info)
            last_hash = self._last_sent_data_hash.get(service_info.address)
            
            # 如果数据相同（签名相同），跳过发送
            if current_hash == last_hash:
                # 数据未变化，不发送（RSSI变化会被忽略）
                return
            
            # 数据有变化，更新签名并发送
            self._last_sent_data_hash[service_info.address] = current_hash
            
            # 通过服务调用发送广播数据给 clife_home 集成
            self.hass.async_create_task(
                self._send_ble_data_to_service(device_info)
            )

        # 订阅所有蓝牙广播（不设置任何过滤条件）
        # connectable=False 表示接收所有广播（包括不可连接的）
        # BluetoothScanningMode.ACTIVE 表示主动扫描模式
        self._cancel_bt_cb = async_register_callback(
            self.hass,
            _bt_callback,
            BluetoothCallbackMatcher({"connectable": False}),
            BluetoothScanningMode.ACTIVE,
        )

    async def _send_ble_data_to_service(self, device_info: Dict) -> None:
        """通过服务调用发送 BLE 广播数据给 clife_home 集成."""
        try:
            # 调用 clife_home 集成的 submit_ble_data 服务  submit_ble_data
            await self.hass.services.async_call(
                "clife_home",
                "submit_ble_data",
                service_data=device_info,
                blocking=False,  # 非阻塞调用，避免影响扫描性能
            )
        except Exception as e:
            # 如果服务不存在或调用失败，记录错误但不影响扫描
            _LOGGER.debug("调用 clife_home.submit_ble_data 服务失败: %s", e)

    @callback
    def _update_device(self, device_info: Dict) -> None:
        """Update device information in the devices dictionary."""
        is_new = device_info["address"] not in self._devices
        self._devices[device_info["address"]] = device_info
        
        # 如果是新设备，通知所有注册的回调函数
        if is_new:
            for callback_func in self._update_callbacks:
                try:
                    callback_func()
                except Exception as e:
                    _LOGGER.error("执行更新回调时出错: %s", e)
    
    def register_update_callback(self, callback_func: Callable[[], None]) -> None:
        """注册更新回调函数，当发现新设备时会被调用."""
        if callback_func not in self._update_callbacks:
            self._update_callbacks.append(callback_func)
    
    def unregister_update_callback(self, callback_func: Callable[[], None]) -> None:
        """取消注册更新回调函数."""
        if callback_func in self._update_callbacks:
            self._update_callbacks.remove(callback_func)
    
    def register_entity_callback(self, callback_func: Callable[[Dict], None]) -> None:
        """注册实体回调函数，当发现或更新Gait设备时会被调用."""
        if callback_func not in self._entity_callbacks:
            self._entity_callbacks.append(callback_func)
    
    def unregister_entity_callback(self, callback_func: Callable[[Dict], None]) -> None:
        """取消注册实体回调函数."""
        if callback_func in self._entity_callbacks:
            self._entity_callbacks.remove(callback_func)

    @callback
    def _restart_scan_periodically(self, now: datetime) -> None:
        """定时器回调：每隔4秒停止并重启扫描."""
        if not self._scanning:
            return
        
        # 停止当前扫描
        if self._cancel_bt_cb:
            try:
                self._cancel_bt_cb()
            except Exception as e:
                _LOGGER.error("停止扫描时出错: %s", e)
            self._cancel_bt_cb = None
        
        # 重新启动扫描
        if self._scanning:
            self.hass.async_create_task(self._restart_scan())
    
    async def _restart_scan(self) -> None:
        """重新启动扫描（异步任务）."""
        try:
            await self._start_ha_bluetooth_scanning()
        except Exception as e:
            _LOGGER.error("重新启动扫描时出错: %s", e, exc_info=True)

    async def async_stop(self) -> None:
        """Stop scanning."""
        self._scanning = False

        # 停止 HA 蓝牙集成回调
        if self._cancel_bt_cb:
            try:
                self._cancel_bt_cb()
            except Exception as e:
                _LOGGER.error("取消蓝牙回调时出错: %s", e)
            self._cancel_bt_cb = None

        # 取消扫描重启定时器
        if self._restart_scan_cancel:
            self._restart_scan_cancel()
            self._restart_scan_cancel = None

        _LOGGER.info("BLE 扫描器已停止")
