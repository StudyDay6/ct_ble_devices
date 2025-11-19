"""Bluetooth scanner for CT BLE Devices."""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    BluetoothChange,
    BluetoothScanningMode,
    async_register_callback,
    MONOTONIC_TIME,
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
        # 广播统计：每个设备的广播次数
        self._broadcast_stats: Dict[str, int] = {}
        # 实体创建回调：用于通知sensor平台创建/更新实体
        self._entity_callbacks: list[Callable[[Dict], None]] = []
        # 存储匹配 "Gait--D6090F310EF5" 的广播数据
        self._gait_data: List[Dict] = []
        self._gait_data_start_time: Optional[float] = None
        self._gait_print_task: Optional[asyncio.Task] = None
        # 扫描重启定时器取消回调
        self._restart_scan_cancel: Optional[Callable[[], None]] = None
        # 数据收集锁，防止并发问题
        self._gait_data_lock = asyncio.Lock()

    @property
    def devices(self) -> Dict[str, Dict]:
        """Return discovered devices."""
        return self._devices

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


    @callback
    def _process_device_broadcast(self, device_info: Dict) -> None:
        """处理设备广播并打印信息."""
        address = device_info["address"]
        
        # 统计广播次数
        if address not in self._broadcast_stats:
            self._broadcast_stats[address] = 0
        self._broadcast_stats[address] += 1
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        broadcast_count = self._broadcast_stats[address]
        
        # 打印设备信息
        _LOGGER.info("=" * 80)
        _LOGGER.info("[%s] 📡 BLE 广播 #%d", timestamp, broadcast_count)
        _LOGGER.info("[%s]   设备名称: %s", timestamp, device_info["name"])
        _LOGGER.info("[%s]   设备地址: %s", timestamp, device_info["address"])
        _LOGGER.info("[%s]   RSSI: %s dBm", timestamp, device_info["rssi"])
        
        # 打印制造商数据
        if device_info["manufacturer_data"]:
            _LOGGER.info("[%s]   制造商数据:", timestamp)
            for manufacturer_id, data in device_info["manufacturer_data"].items():
                data_hex = data.hex() if isinstance(data, bytes) else str(data)
                _LOGGER.info(
                    "[%s]     - ID: 0x%04X, 数据: %s",
                    timestamp,
                    manufacturer_id,
                    data_hex,
                )
        else:
            _LOGGER.info("[%s]   制造商数据: 无", timestamp)
        
        # 打印服务数据
        if device_info["service_data"]:
            _LOGGER.info("[%s]   服务数据:", timestamp)
            for service_uuid, data in device_info["service_data"].items():
                data_hex = data.hex() if isinstance(data, bytes) else str(data)
                _LOGGER.info(
                    "[%s]     - UUID: %s, 数据: %s",
                    timestamp,
                    service_uuid,
                    data_hex,
                )
        else:
            _LOGGER.info("[%s]   服务数据: 无", timestamp)
        
        # 打印服务 UUID 列表
        if device_info["service_uuids"]:
            _LOGGER.info(
                "[%s]   服务 UUID: %s",
                timestamp,
                ", ".join(device_info["service_uuids"]),
            )
        else:
            _LOGGER.info("[%s]   服务 UUID: 无", timestamp)
        
        _LOGGER.info("=" * 80)
        
        # 更新设备信息
        self._update_device(device_info)

    async def _start_ha_bluetooth_scanning(self) -> None:
        """Start scanning using HA bluetooth callbacks - no filtering."""
        @callback
        def _bt_callback(service_info: BluetoothServiceInfoBleak, change: BluetoothChange) -> None:
            """Callback for all BLE advertisements - filter by device name prefix."""
            if change != BluetoothChange.ADVERTISEMENT:
                return
            
            # 获取设备名称
            name = service_info.name or service_info.advertisement.local_name or ""
            # _LOGGER.info("发现设备---- %s", name)
            # 只处理名称前缀为 "Gait Module" 的设备
            if not name.startswith("Gait"):
                return
            
            # 检查广播数据是否过期（设备关闭后，蓝牙栈可能会报告缓存的旧数据）
            # service_info.time 是广播数据的单调时间戳（monotonic time）
            # 如果数据年龄超过阈值，说明是缓存的旧数据，应该忽略
            current_monotonic = MONOTONIC_TIME()
            # advertisement_age = current_monotonic - service_info.time
            # 过期阈值：3秒（超过3秒的数据认为是缓存的旧数据）
            # stale_threshold_seconds = 15.0
            
            # if advertisement_age > stale_threshold_seconds:
            #     _LOGGER.debug(
            #         "忽略过期的广播数据: %s (地址: %s, 数据年龄: %.1f秒, 阈值: %.1f秒)",
            #         name,
            #         service_info.address,
            #         advertisement_age,
            #         stale_threshold_seconds,
            #     )
            #     return
            
            # _LOGGER.info("发现广播---- %s", service_info)
            # _LOGGER.info("发现设备---- %s", name)
            # _LOGGER.info("发现设备---- %s (地址: %s, RSSI: %d, 数据年龄: %.1f秒)", 
            #             name, service_info.address, service_info.rssi, advertisement_age)

            # 处理所有 Gait 设备：创建或更新实体  Gait Module
            if name.startswith("Gait"):
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
            
            # 收集所有名称前缀为Gait设备的放入一个数组
            if name.startswith("Gait"):
                # 使用锁确保线程安全
                async def _add_gait_data():
                    async with self._gait_data_lock:
                        # 记录开始时间（第一次匹配时）
                        if self._gait_data_start_time is None:
                            self._gait_data_start_time = time.time()
                            _LOGGER.info("开始收集所有Gait设备广播数据，将在2秒后打印统计")
                            # 启动2秒后打印的任务
                            # self._gait_print_task = self.hass.async_create_background_task(
                            #     self._print_gait_data_after_delay(),
                            #     "ct_ble_devices_print_gait_data"
                            # )
                        
                        # 只有在收集周期内才添加数据
                        if self._gait_data_start_time is not None:
                            # 保存广播数据
                            broadcast_entry = {
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                                "time_unix": time.time(),
                                "address": service_info.address,
                                "name": name,
                                "rssi": service_info.rssi,
                                "manufacturer_data": dict(service_info.manufacturer_data or {}),
                                "service_data": dict(service_info.service_data or {}),
                                "service_uuids": list(service_info.service_uuids or []),
                                "tx_power": getattr(service_info, "tx_power", None),
                                "source": service_info.source,
                            }
                            self._gait_data.append(broadcast_entry)
                
                # 在事件循环中执行，避免阻塞回调
                self.hass.async_create_task(_add_gait_data())
            # 获取设备名称
            name = service_info.name or service_info.advertisement.local_name or "Unknown"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            
            # 直接打印设备信息
            _LOGGER.info("=" * 80)
            _LOGGER.info("[%s] 📡 BLE 广播", timestamp)
            _LOGGER.info("[%s]   设备名称: %s", timestamp, name)
            _LOGGER.info("[%s]   设备地址: %s", timestamp, service_info.address)
            _LOGGER.info("[%s]   RSSI: %s dBm", timestamp, service_info.rssi)
            
            # 打印制造商数据
            if service_info.manufacturer_data:
                _LOGGER.info("[%s]   制造商数据:", timestamp)
                for manufacturer_id, data in service_info.manufacturer_data.items():
                    data_hex = data.hex() if isinstance(data, bytes) else str(data)
                    _LOGGER.info(
                        "[%s]     - ID: 0x%04X, 数据: %s",
                        timestamp,
                        manufacturer_id,
                        data_hex,
                    )
            else:
                _LOGGER.info("[%s]   制造商数据: 无", timestamp)
            
            # 打印服务数据
            if service_info.service_data:
                _LOGGER.info("[%s]   服务数据:", timestamp)
                for service_uuid, data in service_info.service_data.items():
                    data_hex = data.hex() if isinstance(data, bytes) else str(data)
                    _LOGGER.info(
                        "[%s]     - UUID: %s, 数据: %s",
                        timestamp,
                        service_uuid,
                        data_hex,
                    )
            else:
                _LOGGER.info("[%s]   服务数据: 无", timestamp)
            
            # 打印服务 UUID 列表
            if service_info.service_uuids:
                _LOGGER.info(
                    "[%s]   服务 UUID: %s",
                    timestamp,
                    ", ".join(service_info.service_uuids),
                )
            else:
                _LOGGER.info("[%s]   服务 UUID: 无", timestamp)
            
            _LOGGER.info("=" * 80)

        # 订阅所有蓝牙广播（不设置任何过滤条件）
        # connectable=False 表示接收所有广播（包括不可连接的）
        # BluetoothScanningMode.ACTIVE 表示主动扫描模式
        self._cancel_bt_cb = async_register_callback(
            self.hass,
            _bt_callback,
            BluetoothCallbackMatcher({"connectable": False}),  # 不设置任何过滤，接收所有设备
            BluetoothScanningMode.ACTIVE,
        )
        # _LOGGER.info("已启动蓝牙扫描 - 接收所有设备广播（无过滤）")

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

    # async def _async_cleanup_old_devices(self, now=None) -> None:
    #     """Clean up devices that haven't been seen recently."""
    #     # 可以在这里实现清理逻辑，比如移除超过一定时间未更新的设备
    #     pass

    async def _print_gait_data_after_delay(self) -> None:
        """在2秒后打印收集的所有Gait设备数据统计."""
        # await asyncio.sleep(2)  # 等待2秒
        
        # 使用锁确保清空操作的原子性
        async with self._gait_data_lock:
            if self._gait_data_start_time is None:
                return
            
            data_count = len(self._gait_data)
            elapsed_time = time.time() - self._gait_data_start_time
            
            _LOGGER.info("=" * 80)
            _LOGGER.info("📊 所有Gait设备广播数据统计（2秒后）")
            _LOGGER.info("收集时间: %.2f 秒", elapsed_time)
            _LOGGER.info("数据总数（清空前）: %d 条", data_count)
            _LOGGER.info("=" * 80)
            
            # 清空数据，准备下一轮收集
            self._gait_data.clear()
            # 立即检查清空后的长度
            after_clear_count = len(self._gait_data)
            self._gait_data_start_time = None
            self._gait_print_task = None
            
            if after_clear_count > 0:
                _LOGGER.warning("⚠️ 警告：清空后仍有 %d 条数据，可能存在并发问题！", after_clear_count)

    @callback
    def _restart_scan_periodically(self, now: datetime) -> None:
        """定时器回调：每隔4秒停止并重启扫描."""
        if not self._scanning:
            return
        
        _LOGGER.info("开始重启扫描（定时器触发）")
        
        # 停止当前扫描
        if self._cancel_bt_cb:
            try:
                self._cancel_bt_cb()
            except Exception as e:
                _LOGGER.error("停止扫描时出错: %s", e)
            self._cancel_bt_cb = None
        
        # 启动打印任务
        self._gait_print_task = self.hass.async_create_background_task(
            self._print_gait_data_after_delay(),
            "ct_ble_devices_print_gait_data"
        )
        
        # 重新启动扫描
        if self._scanning:
            self.hass.async_create_task(self._restart_scan())
    
    async def _restart_scan(self) -> None:
        """重新启动扫描（异步任务）."""
        try:
            await self._start_ha_bluetooth_scanning()
            _LOGGER.info("扫描已重新启动")
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

        # 取消打印任务
        if self._gait_print_task:
            self._gait_print_task.cancel()
            try:
                await self._gait_print_task
            except asyncio.CancelledError:
                pass
            self._gait_print_task = None

        # 取消扫描重启定时器
        if self._restart_scan_cancel:
            self._restart_scan_cancel()
            self._restart_scan_cancel = None

        _LOGGER.info("BLE 扫描器已停止")
#  S    B    H
#  40  10  10
#40   6 6
#40  
#40
#40
#40
#
#
#
#
#
#
#
#
#
#
#
#
#
#
#